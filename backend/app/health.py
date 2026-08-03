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

from app.models import Capability, CapabilityMember, Component, HealthResult, Remediation

log = logging.getLogger(__name__)

# Trunk subsystems: a failure here takes down many limbs at once, so they are
# blast_radius=multi and surface first/most prominently.
_TRUNK = {"anthropic_api", "postgres", "worker_scheduler", "email_ingest",
          # A dead evaluator does not break location or email — it breaks KNOWING,
          # which invalidates every other reading on the page at once. That is a
          # multi-limb blast in the only sense that matters here, and trunk renders
          # first, which is exactly where "the monitor is down" belongs.
          "health_evaluator"}

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
    # F2 (capability orders): was `published_expiry`, which was DEFERRED and never
    # built — Google refresh tokens publish no expiry — so this row returned
    # `unknown` forever and any capability naming it as primary could never be
    # green. Audit-derived liveness is the same shape every other external API
    # uses, and it can actually return `ok`. Residual, stated: with no recent
    # Google call it reads `unknown` (no evidence), which is honest — unlike a
    # permanent unknown that no usage could ever clear.
    {"name": "google_oauth",            "kind": "external_api", "depends_on": "GOOGLE_OAUTH_REFRESH_TOKEN", "check_type": "liveness", "description": "Contacts/Tasks/Docs/Sheets (OAuth)."},
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
    # F1 (capability orders): the health system's row for ITSELF. Without it,
    # "self-health: ok" means only "what health checks depend on is ok" — never
    # "health checking is running" — so an evaluator that silently stopped would
    # read green. `stale_seconds` is 3x the 300s evaluator interval: one missed
    # cycle is a slow tick, three is a dead evaluator.
    {"name": "health_evaluator", "kind": "internal_subsystem", "depends_on": "postgres",
     "check_type": "health_evaluator", "check_config": {"stale_seconds": 900},
     "description": "Is health checking itself running, and is the capability rollup coherent?"},

    # ── Data feeds ──
    # Location is TWO components on purpose. The old single `location_pings`
    # freshness check read one signal for two different faults — a dead scheduler
    # and a dead phone were indistinguishable, and 07-19 was spent finding out
    # which. Splitting them makes a missing fix attributable from stored state
    # rather than inferred.
    {"name": "location_pull_scheduler", "kind": "data_feed", "depends_on": "worker_scheduler,autoremote",
     "check_type": "location_scheduler", "description": "Is JARVIS asking the phone for a fix?"},
    # The end-to-end signal, deliberately overlapping its two siblings: both can
    # read healthy while the owner has no usable position, and both can read
    # unhealthy while the feed is fresh. Scoped to newest-ping age and nothing else.
    {"name": "location_freshness", "kind": "data_feed", "depends_on": "",
     "check_type": "location_freshness",
     "description": "Is there a recent position fix at all?"},
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
    # `external_api` + a named credential, by analogy to tavily/gmail/duffel/
    # google_maps — the writes go OUT to GitHub and GITHUB_TOKEN is what makes
    # them possible. It is the first `external_api` with a bespoke check_type,
    # which is fine: kind and check_type vary independently here already
    # (location_pull_scheduler is a `data_feed` with its own check).
    #
    # ONE ASYMMETRY, STATED because a silent one is how a ceiling becomes a
    # surprise: unlike its external_api peers this component NEVER reads `down`
    # (see check_github_writes). A failed document commit is not the system
    # being down, and the amber ceiling is deliberate — the same treatment
    # project_hygiene's is given directly above.
    # Bookkeeping, like project_hygiene above and never `down` for the same
    # reason. `internal_subsystem` rather than `external_api`: nothing leaves the
    # box — the failure it watches for is a session started and forgotten.
    {"name": "planning_sessions", "kind": "internal_subsystem", "depends_on": "postgres",
     "check_type": "planning_sessions", "check_config": {"stale_days": 7},
     "description": "Is a planning session rotting — open and untouched, or more than one at once?"},
    {"name": "github_writes", "kind": "external_api", "depends_on": "GITHUB_TOKEN",
     "check_type": "github_writes", "check_config": {"window_days": 7},
     "description": "Document commits + repo creation to GitHub (reads github_write_log)."},
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
    # Keyed to fault codes NO CHECK EMITS, so they could never join and never
    # rendered — a component would go `down` and the status page would show the
    # fault with no guidance at all, which is how the four-day calendar outage
    # was displayed. `check_liveness` emits exactly one code: `call_failed`.
    # Their content is folded into the `call_failed` runbooks below, which DO
    # join, rather than discarded.
    ("duffel", "401"),
    ("tavily", "401"),
    ("twilio", "a2p_rejected"),
    ("google_calendar_svcacct", "auth_invalid"),
    ("google_oauth", "token_expired"),
    ("google_oauth", "token_missing_scope"),
}

# (component, fault_code) -> runbook. The "place to start" (TDD §4.2 / build §2.1).
_REMEDIATIONS: list[dict] = [
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
    # ONE fault code, deliberately, not create/commit split. `github_write_log`
    # does distinguish operations — but a CheckResult carries exactly one
    # fault_code, so a window holding both a failed create and a failed commit
    # would have to pick one and mislead about the other. The operation is in
    # the DETAIL, where it can name all of them; the runbook covers all four §7
    # starting points. A fault code the evidence cannot cleanly separate is the
    # orphan the join guard exists to catch.
    {"component": "planning_sessions", "fault_code": "session_stalled", "severity": "info",
     "runbook": "A planning session is open and going stale, or more than one is open at "
                "once. Ask JARVIS 'planning status' — it names the slots still missing and "
                "the question that would fill each. Then either (1) keep going: add notes "
                "until the gate reports ready, (2) write it up: 'emit the TDD' — it refuses "
                "if it isn't ready, so trying costs nothing, or (3) let it go: "
                "abandon_planning with a reason (the notes are kept either way). More than "
                "one open is the more urgent case — a note sent from a phone has no "
                "unambiguous home until it's resolved."},
    {"component": "planning_sessions", "fault_code": "no_evidence", "severity": "info",
     "runbook": "No planning session has ever been opened, so there is nothing to judge — "
                "`unknown`, NOT a fault and NOT a green. Expected before the feature has "
                "been used for the first time."},
    {"component": "github_writes", "fault_code": "write_failed", "severity": "warn",
     "runbook": "A GitHub write didn't land. The check's detail names the operation and "
                "target — start there, then work down: (1) token — is GITHUB_TOKEN still "
                "valid and does it carry `repo` scope? An expired or rotated PAT fails every "
                "write identically. (2) rate limit — GitHub returns 403 when exhausted; it "
                "clears on its own, so a 403 burst that stops is not a fault to chase. "
                "(3) repo existence — a repo renamed, deleted, or made private out from under "
                "a recorded repo_url fails on create AND commit. (4) branch conflict — a "
                "docs/<slug>-<date> branch already present from a half-finished run; the commit "
                "path is idempotent, so re-running is safe. Full rows are in github_write_log "
                "(never contains a secret value — see the scanner's non-echo invariant)."},
    {"component": "github_writes", "fault_code": "no_evidence", "severity": "info",
     "runbook": "Nothing has been written to GitHub in the window, so there is nothing to "
                "judge — this is `unknown`, NOT a fault, and not a green either. Expected on "
                "a fresh system or a quiet week. If you believe writes SHOULD have happened, "
                "the question is upstream: did commit_document or create_project_repo actually "
                "run? Check actions_audit for the tool call — its absence there means the tool "
                "was never invoked, which is a different problem from a write that failed."},
    {"component": "location_freshness", "fault_code": "stale_during_active", "severity": "warn",
     "runbook": "No recent position fix during active hours. This check knows only that "
                "fixes STOPPED — it cannot see which layer, and naming one would mis-route "
                "the investigation. Run the tabled diagnostic "
                "(docs/DIAGNOSTIC-location-stale-2026-08-03.md): one query over "
                "location_requests LEFT JOIN location_pings splits it four ways — no "
                "scheduled rows (the server stopped asking), relay_accepted=false "
                "(dispatch), rows with no linked ping (phone silent), or rows with a "
                "linked ping and a large latency (phone late). Do not read latency from "
                "responded_at on historical rows; it is NULL for late answers before "
                "2026-08-03."},
    {"component": "location_freshness", "fault_code": "never_pinged", "severity": "info",
     "runbook": "No position fix has ever been recorded — the phone has not been enrolled "
                "yet. Expected on a fresh system and NOT a fault to chase. Enrolment is "
                "docs/tasker-setup-and-recovery.md: AutoRemote installed, a Tasker event "
                "profile filtering on the nonce regex ^[A-Za-z0-9_-]{22}$ (Use Regex ON, "
                "Exact Message OFF), and the task POSTing to /api/location."},
    {"component": "location_responsiveness", "fault_code": "answering_late", "severity": "warn",
     "runbook": "The phone IS answering, but after the request has already timed out — so "
                "the fixes arrive and are recorded, they are just late. THIS IS NOT THE "
                "not_answering CHECKLIST AND MUST NOT BE MERGED WITH IT: if the Tasker "
                "config were wrong, nothing would answer at all. Something answered, so "
                "the config is demonstrably fine and re-checking it wastes the "
                "diagnosis. Look at POWER MANAGEMENT on the phone: battery optimization "
                "set to Unrestricted for BOTH Tasker and AutoRemote, the doze allowlist / "
                "'allow background activity', and Tasker's exact-alarm permission. Also "
                "ask what CHANGED since the last clean cadence — an OS update, an app "
                "update, or a battery-saver toggle the phone may have applied on its own. "
                "A bimodal latency spread (instant or ~1h, nothing between) points at "
                "doze; a continuous spread points at ordinary delivery contention and may "
                "not be actionable at all."},
    {"component": "location_responsiveness", "fault_code": "not_answering", "severity": "warn",
     "runbook": "The server is asking and the phone is not answering. PHONE-SIDE. Confirm AutoRemote "
                "is installed and receiving (send a test from the AutoRemote web console); the Tasker "
                "Event profile is enabled and its filter matches the NONCE PATTERN "
                "^[A-Za-z0-9_-]{22}$ (the message is the bare nonce — there is no 'jarvis_locreq' "
                "command word and no '=:=' separator); the task reads %arpar1; Tasker location "
                "permission is 'Allow all the "
                "time'; Tasker battery is Unrestricted. See docs/tasker-setup-and-recovery.md."},
    # `call_failed` is what check_liveness actually emits. The two runbooks above
    # for google_calendar_svcacct/google_oauth are keyed to fault codes NO check
    # produces (`auth_invalid`, `token_expired`), so they could never join — a live
    # calendar outage rendered with no runbook at all. Keyed to the real code now;
    # the aspirational ones are left in place for when a check emits them.
    {"component": "google_calendar_svcacct", "fault_code": "call_failed", "severity": "critical",
     "runbook": "Calendar calls are failing. Read the evidence rows: `invalid_grant: Token has "
                "been expired or revoked` means the OAuth credential died — re-consent with "
                "`cd backend && python -m app.google_oauth --client-secrets <path>` then "
                "`fly secrets set GOOGLE_OAUTH_REFRESH_TOKEN=<new>`. Confirm FIRST which auth "
                "path scheduling.py is using (OAuth `_service()` vs the service-account "
                "fallback) — the fix differs by path, and a service-account fault is instead "
                "'re-share the calendar with the service-account email'."},
    {"component": "google_oauth", "fault_code": "call_failed", "severity": "critical",
     "runbook": "A Google OAuth call failed (Docs/Sheets/Contacts/Tasks). Two causes, and the "
                "evidence rows tell them apart: `invalid_grant` = the refresh token is dead or "
                "revoked; a 403 naming a scope = the token was minted before that scope was "
                "added. Both are fixed the same way — re-consent with `cd backend && python -m "
                "app.google_oauth --client-secrets <path>`, then `fly secrets set "
                "GOOGLE_OAUTH_REFRESH_TOKEN=<new>`. Adding a scope in code does NOT update an "
                "existing token; it must be re-minted."},

    # ── `call_failed` for every remaining liveness component ─────────────────
    #
    # `check_liveness` emits exactly ONE fault code. Before this, eight components
    # could go `down` with no runbook at all — the status page would name the fault
    # and then say nothing about it, which is what a real four-day calendar outage
    # actually looked like. A runbook per emittable (component, code) is now
    # enforced by test, so this cannot silently recur as checks are added.
    #
    # Each says what the failure MEANS for the user before what to do about it: a
    # runbook that opens with a command assumes you already know why you are here.
    {"component": "anthropic_api", "fault_code": "call_failed", "severity": "critical",
     "runbook": "THE LLM IS FAILING — every agent is affected, so expect this to be the real "
                "cause of unrelated-looking faults elsewhere. Check status.anthropic.com first; "
                "then ANTHROPIC_API_KEY (`fly secrets list` shows presence, not value) and "
                "whether the account is rate-limited or out of credit. Read the evidence rows: "
                "a 401 is the key, a 429 is rate limiting, a 529 is upstream overload and "
                "usually clears itself."},
    {"component": "twilio", "fault_code": "call_failed", "severity": "critical",
     "runbook": "SMS or voice is failing. VOICE AND SMS FAIL INDEPENDENTLY and share this one "
                "component — read the evidence rows to see which. SMS blocked by A2P: "
                "re-register the brand under the EIN as a business and resubmit the campaign "
                "with business framing; voice is unaffected by that. Otherwise check "
                "TWILIO_AUTH_TOKEN / TWILIO_ACCOUNT_SID, the number's capabilities, and account "
                "balance."},
    {"component": "gmail", "fault_code": "call_failed", "severity": "critical",
     "runbook": "Outbound email is failing — drafts and confirmations will not be delivered. "
                "GMAIL_APP_PASSWORD is an APP PASSWORD, not the account password, and it dies "
                "when 2FA is reset or the app password is revoked. Regenerate at "
                "myaccount.google.com/apppasswords and `fly secrets set GMAIL_APP_PASSWORD=<new>`."},
    {"component": "google_maps", "fault_code": "call_failed", "severity": "warn",
     "runbook": "Directions/Places are failing — traffic drops out of the brief and 'near me' "
                "stops resolving. Usually billing rather than the key: confirm the Cloud project "
                "still has billing enabled and that Directions API + Places API are both "
                "enabled. Then check GOOGLE_MAPS_API_KEY and any HTTP-referrer restriction on it."},
    {"component": "tavily", "fault_code": "call_failed", "severity": "warn",
     "runbook": "Web research is failing — the researcher agent returns nothing and the news "
                "section drops from the brief. A 401 is the key; a 432/quota response means the "
                "plan is out of credits. Check TAVILY_API_KEY and the plan balance at "
                "tavily.com."},
    {"component": "duffel", "fault_code": "call_failed", "severity": "warn",
     "runbook": "Flight search/booking is failing. A 401 means Duffel rejected the key — check "
                "DUFFEL_API_KEY, and if it is a LIVE-mode key confirm the account is activated "
                "and has prepaid balance (test keys work while live keys 401 on an "
                "un-activated account). Note booking is gated behind `booking_enabled` "
                "separately, so this can fail while nothing user-visible is broken."},
    {"component": "alpaca", "fault_code": "call_failed", "severity": "warn",
     "runbook": "Market data is failing. Check ALPACA_API_KEY / ALPACA_SECRET_KEY and that the "
                "base URL matches the key's environment (paper keys against the live endpoint "
                "401, and vice versa). Read-only market data only — no order path is affected."},
    {"component": "nws", "fault_code": "call_failed", "severity": "info",
     "runbook": "Weather/marine is failing — those sections drop out of the brief, which is a "
                "degraded brief, not a broken one. NWS needs no API key, so this is almost "
                "always upstream: check api.weather.gov. It also 404s legitimately for a "
                "point outside US coverage, so confirm the configured home address first."},
    # postgres runs check_app_up (which OVERRIDES its check_type), so it cannot emit
    # `call_failed`. Its only failure mode is the check raising — which is exactly
    # what an unreachable database looks like from inside `SELECT 1`.
    {"component": "postgres", "fault_code": "check_error", "severity": "critical",
     "runbook": "THE DATABASE IS UNREACHABLE — `SELECT 1` raised. Nothing durable is being "
                "written: no audit rows, no health results, no jobs. Every other status on this "
                "page is suspect because they are all read from it. Check `fly status -a "
                "jarvis-mdk` and the Postgres attachment; confirm DATABASE_URL is set and the "
                "database has not run out of disk."},
    # The meta-check's own faults. A runbook here is load-bearing in a way the
    # others are not: when this fires, every other reading on the page is suspect.
    {"component": "health_evaluator", "fault_code": "evaluator_stale", "severity": "critical",
     "runbook": "Health checking has STOPPED. Every other status on this page is stale and "
                "must not be trusted — including the greens. The evaluator rides the worker "
                "tick, so this usually means the worker is dead: check `worker_scheduler` "
                "first, then `fly apps restart jarvis-mdk`, then confirm the log line "
                "`health cycle: N components in Xms`."},
    {"component": "health_evaluator", "fault_code": "rollup_incoherent", "severity": "warn",
     "runbook": "The capability rollup references components that do not exist, are disabled, "
                "or a capability has no primary member. The rollup is still reporting, but at "
                "least one capability's answer is built on a missing part. Fix the seed in "
                "`app/health.py` (_CAPABILITIES / _CAPABILITY_MEMBERS) — the detail names the "
                "offending capability and component."},
]


# ── Capabilities (TDD §4; seed ratified 2026-07-31) ──────────────────────────
#
# A capability is what the owner would say JARVIS can DO. Components are parts;
# this is what the parts add up to from outside. Both are needed: "postgres is up"
# does not answer "can you tell me where I am".
#
# PRIMARY is the member whose `down` makes the capability RED. Everything else
# makes it AMBER. Derived rule, stated because it is load-bearing and was NOT
# given: a primary that is `degraded` yields AMBER, not red — otherwise
# project_hygiene (which is degraded-or-better by design) would red the project
# capability and contradict the ratified amber ceiling.
#
# Trunk components are EXPLICIT-ONLY members (decision 8): a capability lists
# postgres/anthropic_api only where genuinely load-bearing, and trunk faults render
# ABOVE the rollup rather than reddening eight rows with one fault.
_CAPABILITIES: list[dict] = [
    {"name": "location", "label": "Location", "lifecycle": "live",
     "description": "Knowing where the owner is, on demand and on a schedule.",
     "notes": ""},
    {"name": "calendar", "label": "Calendar", "lifecycle": "live",
     "description": "Reading the schedule and creating events."},
    {"name": "morning_brief", "label": "Morning brief", "lifecycle": "live",
     "description": "The daily brief: schedule, weather, marine, traffic, tasks, news.",
     "notes": "Content sources are non-primary on purpose: a brief that goes out "
              "without its weather section is degraded, a brief that never fires is "
              "broken. Only the scheduler can make this red."},
    {"name": "project_tracking", "label": "Project tracking", "lifecycle": "live",
     "description": "Projects, milestones, and whether the records still tell the truth.",
     "notes": "AMBER CEILING BY DESIGN — this capability has no red path, and that is "
              "ratified, not an oversight. project_hygiene is informational and never "
              "returns `down`: the tools are ungated DB reads/writes with no user-visible "
              "outage mode, so a drifting project record is a bookkeeping problem, not a "
              "system fault. Manufacturing a red path for symmetry would put bookkeeping "
              "beside a dead scheduler and train the eye to skip both."},
    {"name": "memory", "label": "Memory", "lifecycle": "live",
     "description": "Facts, episodes, and recall.",
     "notes": "KNOWN BLIND SPOT: vectorstore/embedding health is uninstrumented, so a "
              "semantic-recall failure is invisible to this rollup — stored memory would "
              "still read green while recall silently returned nothing. Pending a "
              "`vectorstore` component."},
    {"name": "voice_sms", "label": "Voice + SMS", "lifecycle": "live",
     "description": "Inbound/outbound calls and text messages.",
     "notes": "ONE capability by design, not an oversight. Twilio voice and A2P SMS do "
              "fail independently, but there is exactly one `twilio` component covering "
              "both — so two capabilities would share one member, always agree, and the "
              "split would be cosmetic. Splitting requires splitting the COMPONENT first "
              "(twilio_voice / twilio_sms, each with its own check and runbook)."},
    {"name": "self_health", "label": "Self-health monitoring", "lifecycle": "live",
     "description": "Is JARVIS watching herself, and is that watching trustworthy?",
     "notes": "Seeded only because `health_evaluator` now exists (F1). Before it, this "
              "capability would have reported on everything except whether it was running."},
    {"name": "contacts", "label": "Contacts / People", "lifecycle": "live",
     "description": "Contact lookup and Google People sync.",
     "notes": "Seeded only because google_oauth moved from the never-built "
              "`published_expiry` check to audit-derived liveness (F2). It reads `unknown` "
              "if no Google call has happened in the liveness window — honest absence of "
              "evidence, which usage clears."},

    # ── Gated: excluded from the live rollup, reported as not-configured ──
    # Stated honestly rather than omitted, because silent absence is how a
    # capability stops being noticed (the PR #43 netstatus argument).
    {"name": "flight_booking", "label": "Flight booking", "lifecycle": "gated",
     "gated_by": "booking_enabled",
     "description": "Searching and booking flights via Duffel."},
    {"name": "local_network", "label": "Local network", "lifecycle": "gated",
     "gated_by": "",
     "description": "LAN visibility: hypervisor, uptime monitor, tailnet.",
     "notes": "Grouped as ONE gated capability rather than four gated rows — they share a "
              "single cause (unreachable from Fly) and a single fix (be on the LAN), so "
              "four rows would be one fact repeated four times. All members are stubs."},
]

# capability -> [(component, is_primary)]
_CAPABILITY_MEMBERS: dict[str, list[tuple[str, bool]]] = {
    # Primary is responsiveness because it is the END-TO-END signal: a dead
    # scheduler eventually shows up here too, while the reverse is not true.
    # `location_freshness` is NON-PRIMARY. DECIDED 2026-08-03, on evidence rather
    # than argument, and the evidence is the state the capability was in that day:
    #
    #     location_freshness       ok    newest fix 8m old
    #     location_responsiveness  down  1 of 6 answered
    #
    # The loop was broken and the feed was still fresh. If freshness were primary
    # the capability would have read GREEN through that, and the broken loop would
    # have surfaced only once a fix finally went stale — i.e. as an outage rather
    # than as a warning.
    #
    # RESPONSIVENESS LEADS; FRESHNESS LAGS. Primary belongs to the leading
    # indicator. Freshness is the better end-to-end signal and the worse alarm,
    # because by the time it turns the thing it was warning about has happened.
    "location": [("location_responsiveness", True), ("location_pull_scheduler", False),
                 ("location_freshness", False), ("navigator", False)],
    "calendar": [("google_calendar_svcacct", True), ("scheduling", False)],
    "morning_brief": [("worker_scheduler", True), ("gmail", False), ("twilio", False),
                      ("nws", False), ("google_calendar_svcacct", False),
                      ("google_maps", False)],
    "project_tracking": [("project_hygiene", True)],
    # anthropic_api is non-primary: distillation and recall degrade without it,
    # but stored memory stays readable. postgres is primary and IS listed, because
    # here it is genuinely load-bearing rather than ambient (decision 8).
    "memory": [("postgres", True), ("archivist", False), ("anthropic_api", False)],
    "voice_sms": [("twilio", True)],
    "self_health": [("health_evaluator", True), ("postgres", False),
                    ("worker_scheduler", False)],
    "contacts": [("google_oauth", True), ("secretary", False)],
    "flight_booking": [("duffel", True), ("travel", False)],
    "local_network": [("netstatus", True), ("proxmox", False), ("uptime_kuma", False),
                      ("tailscale", False)],
}

# Same lesson as _RETIRED: reconciling never deletes, so a capability dropped from
# the list above would linger and keep being rolled up.
_RETIRED_CAPABILITIES: set[str] = set()

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

    _seed_capabilities(db)

    db.commit()
    log.info("seeded/reconciled %d components, %d remediations, %d capabilities",
             touched, len(_REMEDIATIONS), len(_CAPABILITIES))
    return touched


def _seed_capabilities(db: Session) -> None:
    """Seed + RECONCILE capabilities and their membership.

    Membership is reconciled by REPLACEMENT, not merge: a member removed from
    `_CAPABILITY_MEMBERS` must actually disappear, or a component dropped from a
    capability keeps voting on its status forever. That is the `_RETIRED` lesson
    applied one level up — and it matters more here, because a stale member is not
    merely visible, it silently changes an answer.
    """
    for spec in _CAPABILITIES:
        row = db.get(Capability, spec["name"])
        if row is None:
            row = Capability(name=spec["name"])
            db.add(row)
        row.label = spec.get("label", spec["name"])
        row.lifecycle = spec.get("lifecycle", "live")
        row.gated_by = spec.get("gated_by", "")
        row.description = spec.get("description", "")
        row.notes = spec.get("notes", "")

    for name in _RETIRED_CAPABILITIES:
        row = db.get(Capability, name)
        if row is not None:
            db.delete(row)
            log.info("retired capability %r", name)
        for m in db.query(CapabilityMember).filter(CapabilityMember.capability == name).all():
            db.delete(m)

    for cap, members in _CAPABILITY_MEMBERS.items():
        wanted = {c: p for c, p in members}
        existing = {
            m.component: m
            for m in db.query(CapabilityMember).filter(CapabilityMember.capability == cap).all()
        }
        for component, is_primary in wanted.items():
            m = existing.get(component)
            if m is None:
                m = CapabilityMember(capability=cap, component=component)
                db.add(m)
            m.is_primary = is_primary
        for component, m in existing.items():
            if component not in wanted:
                db.delete(m)
                log.info("removed %s from capability %s", component, cap)


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
