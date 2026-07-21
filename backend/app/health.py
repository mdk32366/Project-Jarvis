"""Health topology — the deterministic system inventory in Postgres (TDD §4).

PR-A is pure reference data + the reconciling seed: the `component` inventory,
the `remediation` fault->runbook map, and the tool->component lookup that lets
audit rows be grouped by the component they belong to (the §4A evidence bridge).
No health checks run here — that's PR-B, which reads `check_type`/`check_config`
off these rows.

Seeding RECONCILES rather than only inserting (the `seed_agents()` lesson, §2.1):
a component's kind/description/check fields are refreshed from code on every
startup, so stale reference data can't silently persist — the exact class of bug
that made a code capability invisible in the DB.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import Component, HealthResult, Remediation

log = logging.getLogger(__name__)

# Trunk subsystems: a failure here takes down many limbs at once, so they are
# blast_radius=multi and surface first/most prominently.
_TRUNK = {"anthropic_api", "postgres", "worker_scheduler", "email_ingest"}

# name, kind, description, depends_on, check_type, check_config
# check_type: liveness | secret_age | published_expiry | heartbeat | freshness | none
_COMPONENTS: list[dict] = [
    # ── Agents (health derives from their APIs; here for the topology + evidence) ──
    {"name": "researcher",  "kind": "agent", "depends_on": "tavily",                  "check_type": "none", "description": "Web research: search + fetch."},
    {"name": "finance",     "kind": "agent", "depends_on": "alpaca",                  "check_type": "none", "description": "Read-only market data + portfolio."},
    {"name": "archivist",   "kind": "agent", "depends_on": "postgres,anthropic_api",  "check_type": "none", "description": "Long-term memory (facts + episodes)."},
    {"name": "infra",       "kind": "agent", "depends_on": "fly_api",                 "check_type": "none", "description": "Hosted Fly apps: health + spend."},
    {"name": "secretary",   "kind": "agent", "depends_on": "gmail,google_oauth",      "check_type": "none", "description": "Email, tasks, docs/sheets, contacts, callbacks, watches, ideas."},
    {"name": "travel",      "kind": "agent", "depends_on": "duffel",                  "check_type": "none", "description": "Flight search + booking."},
    {"name": "navigator",   "kind": "agent", "depends_on": "google_maps",            "check_type": "none", "description": "Traffic, places, where-am-I."},
    {"name": "netstatus",   "kind": "agent", "depends_on": "proxmox,uptime_kuma,tailscale", "check_type": "none", "description": "Local network status (stubbed until on-LAN)."},
    {"name": "scheduling",  "kind": "agent", "depends_on": "google_calendar_svcacct", "check_type": "none", "description": "Calendar lookup + event creation."},

    # ── External APIs ──
    {"name": "tavily",                  "kind": "external_api", "depends_on": "TAVILY_API_KEY",  "check_type": "liveness",          "description": "Web search + page fetch."},
    {"name": "alpaca",                  "kind": "external_api", "depends_on": "ALPACA_API_KEY",  "check_type": "liveness",          "description": "Market data."},
    {"name": "gmail",                   "kind": "external_api", "depends_on": "GMAIL_APP_PASSWORD", "check_type": "liveness",       "description": "Outbound email (SMTP)."},
    {"name": "google_oauth",            "kind": "external_api", "depends_on": "GOOGLE_OAUTH_REFRESH_TOKEN", "check_type": "published_expiry", "description": "Contacts/Tasks/Docs/Sheets (OAuth)."},
    {"name": "google_calendar_svcacct", "kind": "external_api", "depends_on": "GOOGLE_SERVICE_ACCOUNT_JSON", "check_type": "liveness",     "description": "Calendar (service account)."},
    {"name": "duffel",                  "kind": "external_api", "depends_on": "DUFFEL_API_KEY",  "check_type": "liveness",          "description": "Flight search + booking."},
    {"name": "google_maps",             "kind": "external_api", "depends_on": "GOOGLE_MAPS_API_KEY", "check_type": "liveness",      "description": "Directions + Places."},
    {"name": "twilio",                  "kind": "external_api", "depends_on": "TWILIO_AUTH_TOKEN", "check_type": "liveness",        "description": "SMS + voice."},
    {"name": "proxmox",                 "kind": "external_api", "depends_on": "",                "check_type": "none",              "description": "LAN hypervisor — unreachable from Fly (stub)."},
    {"name": "uptime_kuma",             "kind": "external_api", "depends_on": "",                "check_type": "none",              "description": "LAN reachability monitor — unreachable from Fly (stub)."},
    {"name": "tailscale",               "kind": "external_api", "depends_on": "TAILSCALE_API_KEY", "check_type": "none",            "description": "Tailnet device status (stub)."},
    {"name": "nws",                     "kind": "external_api", "depends_on": "",                "check_type": "liveness",          "description": "Weather / marine forecast (National Weather Service)."},

    # ── Internal subsystems (trunk) ──
    {"name": "anthropic_api",   "kind": "internal_subsystem", "depends_on": "ANTHROPIC_API_KEY", "check_type": "liveness",  "description": "Every agent's LLM."},
    {"name": "postgres",        "kind": "internal_subsystem", "depends_on": "DATABASE_URL",      "check_type": "liveness",  "description": "The database — every durable record."},
    {"name": "worker_scheduler", "kind": "internal_subsystem", "depends_on": "",                "check_type": "heartbeat", "check_config": {"stale_seconds": 300}, "description": "Job worker + briefing scheduler."},
    {"name": "email_ingest",    "kind": "internal_subsystem", "depends_on": "",                  "check_type": "none",      "description": "Inbound email ingestion."},

    # ── Data feeds ──
    # Location is TWO components on purpose. The old single `location_pings`
    # freshness check read one signal for two different faults — a dead scheduler
    # and a dead phone were indistinguishable, and 07-19 was spent finding out
    # which. Splitting them makes a missing fix attributable from stored state
    # rather than inferred.
    {"name": "location_pull_scheduler", "kind": "data_feed", "depends_on": "worker_scheduler,autoremote",
     "check_type": "location_scheduler", "description": "Is JARVIS asking the phone for a fix?"},
    {"name": "location_responsiveness", "kind": "data_feed", "depends_on": "",
     "check_type": "location_responsiveness", "check_config": {"window": 6, "ok_min": 5, "degraded_min": 3},
     "description": "Is the phone answering when asked?"},

    # ── Bookkeeping ──
    # Informational only, and NEVER `down` (see check_project_hygiene): a stale
    # project record is a bookkeeping problem, not a system fault, and inflating
    # it would train the eye to ignore the status page — the exact failure mode
    # the exception-first design exists to prevent.
    {"name": "project_hygiene", "kind": "internal_subsystem", "depends_on": "postgres",
     "check_type": "project_hygiene", "check_config": {"stale_days": 30},
     "description": "Are the project records honest — milestones open, one live doc, recently touched?"},
]

# Components removed from _COMPONENTS above. The seed RECONCILES but does not
# delete, so a row dropped from the list would otherwise linger in the database
# still carrying its old check_type — and keep being run and reported. Retiring
# has to be explicit or it doesn't happen.
#
# `location_pings` (freshness-only) is superseded by the two components above.
_RETIRED: set[str] = {"location_pings"}

# (component, fault_code) pairs no longer produced by any check. Same lesson as
# _RETIRED: the seed reconciles runbook TEXT but never removes rows, so a renamed
# fault code would leave its old runbook joinable forever — and that runbook sent
# the reader somewhere the fault no longer lives.
#
# `dispatch_failing` became `relay_rejected` when the column stopped claiming to
# measure delivery (TDD §12).
_RETIRED_REMEDIATIONS: set[tuple[str, str]] = {
    ("location_pull_scheduler", "dispatch_failing"),
}

# (component, fault_code) -> runbook. The "place to start" (TDD §4.2 / build §2.1).
_REMEDIATIONS: list[dict] = [
    {"component": "google_oauth", "fault_code": "token_missing_scope", "severity": "critical",
     "runbook": "Docs/Sheets/Contacts/Tasks scope missing or refresh token dead. "
                "`cd backend && python -m app.google_oauth --client-secrets <path>`, then "
                "`fly secrets set GOOGLE_OAUTH_REFRESH_TOKEN=<new>`."},
    {"component": "google_oauth", "fault_code": "token_expired", "severity": "critical",
     "runbook": "OAuth refresh token expired. Re-consent: "
                "`cd backend && python -m app.google_oauth --client-secrets <path>`, then "
                "`fly secrets set GOOGLE_OAUTH_REFRESH_TOKEN=<new>`."},
    {"component": "google_calendar_svcacct", "fault_code": "auth_invalid", "severity": "critical",
     "runbook": "Service account lost calendar scope or the calendar isn't shared with it. "
                "Re-share the calendar with the service-account email (Make changes to events); "
                "verify scope in scheduling.py."},
    {"component": "duffel", "fault_code": "401", "severity": "warn",
     "runbook": "Duffel rejected the key. Check DUFFEL_API_KEY; if live-mode, confirm activation "
                "and prepaid balance."},
    {"component": "worker_scheduler", "fault_code": "heartbeat_stale", "severity": "critical",
     "runbook": "Worker not reporting (no heartbeat in the staleness window). "
                "`fly apps restart jarvis-mdk`; confirm the log line "
                "`briefing scheduled daily at HH:MM`."},
    # Location, split by fault owner: the two runbooks below point at different
    # machines on purpose. Sending someone to the phone for a server fault is how
    # 07-19 was lost.
    {"component": "location_pull_scheduler", "fault_code": "not_asking", "severity": "warn",
     "runbook": "The server is not requesting location fixes. SERVER-SIDE, not the phone. "
                "Check `location_pull_enabled` on the /status runtime-settings panel; confirm the "
                "worker heartbeat is alive (a dead worker stops pulls too); check `relay_error` "
                "on the most recent location_requests row."},
    {"component": "location_pull_scheduler", "fault_code": "relay_rejected", "severity": "warn",
     "runbook": "Requests are being minted but the AutoRemote relay is refusing them. Read "
                "`relay_error` on the most recent location_requests row — the relay answers HTTP "
                "200 to everything and puts the real outcome in the body. `NotRegistered` means "
                "the key does not match a registered device: re-set AUTOREMOTE_KEY to the BARE "
                "TOKEN (the AutoRemote web page shows it inside a URL, so it is easy to paste the "
                "leading `key=` by mistake — that exact typo disabled the feature silently from "
                "2026-07-19 to 07-21). Fly secrets are write-only; re-set rather than read back."},
    {"component": "project_hygiene", "fault_code": "record_stale", "severity": "info",
     "runbook": "A tracked project's record is drifting from reality. Ask JARVIS "
                "'where am I on <project>' — project_status names the specific problem "
                "(no open milestones, two live docs of one kind, or untouched for 30+ days). "
                "Fix it by completing/adding a milestone, superseding the duplicate document, "
                "or parking the project with a reason."},
    {"component": "location_responsiveness", "fault_code": "not_answering", "severity": "warn",
     "runbook": "The server is asking and the phone is not answering. PHONE-SIDE. Confirm AutoRemote "
                "is installed and receiving (send a test from the AutoRemote web console); the Tasker "
                "Event profile is enabled and its filter matches the NONCE PATTERN "
                "^[A-Za-z0-9_-]{22}$ (the message is the bare nonce — there is no 'jarvis_locreq' "
                "command word and no '=:=' separator); the task reads %arpar1; Tasker location "
                "permission is 'Allow all the "
                "time'; Tasker battery is Unrestricted. See docs/tasker-setup-and-recovery.md."},
    {"component": "twilio", "fault_code": "a2p_rejected", "severity": "warn",
     "runbook": "SMS blocked by A2P. Re-register the brand under the EIN as a business and resubmit "
                "the campaign with business framing. Voice is unaffected."},
    {"component": "tavily", "fault_code": "401", "severity": "warn",
     "runbook": "Tavily rejected the key. Check TAVILY_API_KEY; confirm the plan has credits."},
]

# tool name -> owning component (the §4A evidence join key). Maps to the external
# API where one exists (that's what liveness watches), else the owning subsystem.
_TOOL_COMPONENT: dict[str, str] = {
    "web_search": "tavily", "fetch_page": "tavily",
    "get_stock_price": "alpaca", "get_portfolio": "alpaca",
    "draft_email": "gmail", "send_email": "gmail",
    "calendar_lookup": "google_calendar_svcacct", "create_event": "google_calendar_svcacct",
    "search_flights": "duffel", "list_trips": "duffel", "book_flight": "duffel",
    "get_traffic": "google_maps", "find_place": "google_maps", "where_am_i": "google_maps",
    "get_node_status": "uptime_kuma", "get_service_health": "uptime_kuma",
    "tailscale_status": "tailscale",
    "create_google_doc": "google_oauth", "create_google_sheet": "google_oauth",
    "append_to_google_doc": "google_oauth", "sync_google_contacts": "google_oauth",
    "google_status": "google_oauth",
    "call_me_back": "twilio", "pending_callbacks": "twilio", "cancel_callback": "twilio",
    "remember_fact": "postgres", "recall_facts": "postgres", "forget_fact": "postgres",
    "audit_memory": "postgres", "recall": "postgres", "recall_episodes": "postgres",
    "add_task": "postgres", "list_tasks": "postgres", "complete_task": "postgres",
    "cancel_task": "postgres", "capture_idea": "postgres", "list_ideas": "postgres",
    "get_idea": "postgres", "lookup_contact": "postgres", "save_contact": "postgres",
    "list_contacts": "postgres", "watch_for": "postgres", "list_watches": "postgres",
    "cancel_watch": "postgres",
    "fleet_health": "infra", "fleet_spend": "infra",
}


def component_for_tool(tool: str) -> str | None:
    """Owning component for a tool name (evidence bridge, §4A). A tool may be
    audited bare ('send_email') or agent-prefixed ('secretary:draft_email'); both
    resolve to the same component."""
    if tool in _TOOL_COMPONENT:
        return _TOOL_COMPONENT[tool]
    if ":" in tool:                       # 'agent:tool' from a sub-agent audit row
        return _TOOL_COMPONENT.get(tool.split(":", 1)[1])
    return None


def get_runbook(db: Session, component: str, fault_code: str) -> Remediation | None:
    """Join a (component, fault_code) to its remediation row. Returns None when no
    runbook exists — the caller degrades to a generic message rather than crashing
    (TDD test #31)."""
    return (
        db.query(Remediation)
        .filter(Remediation.component == component, Remediation.fault_code == fault_code)
        .first()
    )


def seed_health_topology(db: Session) -> int:
    """Seed + RECONCILE the component inventory and remediation runbooks. Returns
    the number of component rows touched. Reconciling (not just inserting) keeps
    kind/description/check fields in step with code — the seed_agents() lesson."""
    touched = 0
    for spec in _COMPONENTS:
        cfg = json.dumps(spec.get("check_config", {})) if spec.get("check_config") else ""
        row = db.get(Component, spec["name"])
        if row is None:
            row = Component(name=spec["name"])
            db.add(row)
        row.kind = spec["kind"]
        row.description = spec.get("description", "")
        row.depends_on = spec.get("depends_on", "")
        row.check_type = spec.get("check_type", "none")
        row.blast_radius = "multi" if spec["name"] in _TRUNK else "single"
        row.check_config = cfg
        touched += 1

    # Retire superseded components. Their health_result rows go too — a stale result
    # for a component that no longer exists would keep reporting on the status page
    # long after the check that produced it was deleted.
    for name in _RETIRED:
        row = db.get(Component, name)
        if row is not None:
            db.delete(row)
            log.info("retired health component %r", name)
        stale = db.get(HealthResult, name)
        if stale is not None:
            db.delete(stale)
        for rem_row in db.query(Remediation).filter(Remediation.component == name).all():
            db.delete(rem_row)

    for comp, fault in _RETIRED_REMEDIATIONS:
        stale_rem = (
            db.query(Remediation)
            .filter(Remediation.component == comp, Remediation.fault_code == fault)
            .first()
        )
        if stale_rem is not None:
            db.delete(stale_rem)
            log.info("retired remediation %s/%s", comp, fault)

    for rem in _REMEDIATIONS:
        row = (
            db.query(Remediation)
            .filter(Remediation.component == rem["component"], Remediation.fault_code == rem["fault_code"])
            .first()
        )
        if row is None:
            row = Remediation(component=rem["component"], fault_code=rem["fault_code"])
            db.add(row)
        row.runbook = rem["runbook"]
        row.severity = rem.get("severity", "warn")

    db.commit()
    log.info("seeded/reconciled %d components, %d remediations", touched, len(_REMEDIATIONS))
    return touched


def registry_discrepancies(db: Session) -> dict:
    """Reconciliation report: agents in code but not seeded as components, and
    vice-versa. Surfaces the kind of code/DB drift that hid the roster defect —
    a component/registry discrepancy instead of a silent absence (build §3)."""
    from app.agents import DEFAULT_AGENTS

    code_agents = set(DEFAULT_AGENTS)
    seeded_agents = {
        c.name for c in db.query(Component).filter(Component.kind == "agent").all()
    }
    return {
        "agents_in_code_not_seeded": sorted(code_agents - seeded_agents),
        "agents_seeded_not_in_code": sorted(seeded_agents - code_agents),
    }
