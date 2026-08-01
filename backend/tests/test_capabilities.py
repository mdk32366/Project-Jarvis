"""Capability rollup — what JARVIS can DO, rolled up from component health.

The rules under test are the JUDGMENT content of this build, and two of them were
derived rather than dictated (see app/capabilities.py):

  - primary `down` -> red, but primary `degraded` -> AMBER. Taken literally, "the
    member whose non-ok forces red" would red project tracking on a degraded
    project_hygiene and contradict the amber ceiling ratified in the same orders.
  - a non-primary `unknown` does NOT move the rollup. For a secondary, absence of
    evidence is weaker than evidence of failure, and counting it would paint the
    morning brief permanently amber off gmail/nws simply having no recent calls.

Both are pinned here so a future change to either is deliberate.
"""

import pytest

from app.capabilities import (
    brief_line, coherence_problems, evaluate, summary,
)
from app.health import seed_health_topology
from app.models import Capability, CapabilityMember, Component, HealthResult


@pytest.fixture
def seeded(db):
    seed_health_topology(db)
    return db


def _set(db, component: str, status: str, fault: str | None = None) -> None:
    row = db.get(HealthResult, component)
    if row is None:
        row = HealthResult(component=component)
        db.add(row)
    row.status = status
    row.fault_code = fault
    row.detail = f"test-set {status}"
    db.commit()


def _cap(rollup, name):
    return next(c for c in rollup if c["name"] == name)


# ── the seed itself ──────────────────────────────────────────────────────────

def test_seed_creates_the_ratified_capability_set(seeded):
    caps = {c.name: c for c in seeded.query(Capability).all()}
    live = {n for n, c in caps.items() if c.lifecycle == "live"}
    gated = {n for n, c in caps.items() if c.lifecycle == "gated"}

    assert live == {"location", "calendar", "morning_brief", "project_tracking",
                    "memory", "voice_sms", "self_health", "contacts"}
    assert gated == {"flight_booking", "local_network"}


def test_every_capability_has_exactly_one_primary(seeded):
    """The rule the whole rollup rests on. A capability with no primary cannot be
    judged; one with two has no defined red condition."""
    for cap in seeded.query(Capability).all():
        primaries = (
            seeded.query(CapabilityMember)
            .filter(CapabilityMember.capability == cap.name,
                    CapabilityMember.is_primary.is_(True))
            .count()
        )
        assert primaries == 1, cap.name


def test_every_member_is_a_real_component(seeded):
    """A member naming a component that does not exist still produces a confident
    answer, assembled from a part nobody checks."""
    names = {c.name for c in seeded.query(Component).all()}
    for m in seeded.query(CapabilityMember).all():
        assert m.component in names, f"{m.capability} -> {m.component}"


def test_seed_reconciles_membership_by_replacement(seeded):
    """A member dropped from the code seed must actually disappear. A stale member
    is not merely visible — it silently keeps voting on a capability's status."""
    seeded.add(CapabilityMember(capability="voice_sms", component="postgres",
                                is_primary=False))
    seeded.commit()

    seed_health_topology(seeded)

    members = {m.component for m in seeded.query(CapabilityMember)
               .filter(CapabilityMember.capability == "voice_sms").all()}
    assert members == {"twilio"}


# ── the rollup rules ─────────────────────────────────────────────────────────

def test_primary_down_is_red(seeded):
    _set(seeded, "twilio", "down", "call_failed")
    assert _cap(evaluate(seeded), "voice_sms")["status"] == "red"


def test_primary_degraded_is_amber_not_red(seeded):
    """DERIVED RULE. Degraded means impaired, not broken — and reading it as red
    would contradict the ratified amber ceiling below."""
    _set(seeded, "twilio", "degraded", "something")
    assert _cap(evaluate(seeded), "voice_sms")["status"] == "amber"


def test_non_primary_fault_is_amber_never_red(seeded):
    """A brief with no weather section is degraded; a brief that never fires is
    broken. One word for both teaches the reader to ignore it."""
    _set(seeded, "worker_scheduler", "ok")
    _set(seeded, "nws", "down", "call_failed")
    brief = _cap(evaluate(seeded), "morning_brief")
    assert brief["status"] == "amber"
    assert brief["driving_member"] == "nws"


def test_non_primary_unknown_does_not_move_the_rollup(seeded):
    """DERIVED RULE. gmail and nws sit at `unknown` in production for the benign
    reason that nothing called them recently. Counting that as amber would paint
    the brief permanently amber and train the eye to skip it."""
    _set(seeded, "worker_scheduler", "ok")
    for c in ("gmail", "nws", "twilio", "google_calendar_svcacct", "google_maps"):
        _set(seeded, c, "unknown", "no_evidence")

    brief = _cap(evaluate(seeded), "morning_brief")
    assert brief["status"] == "ok"
    # ...but the unknowns are still reported, not hidden.
    assert {m["component"] for m in brief["members"] if m["status"] == "unknown"}


def test_primary_unknown_is_unknown_never_green(seeded):
    """`unknown` is not a pass. A capability whose primary has no evidence has no
    basis to claim it works."""
    _set(seeded, "google_oauth", "unknown", "no_evidence")
    assert _cap(evaluate(seeded), "contacts")["status"] == "unknown"


def test_project_tracking_has_no_red_path(seeded):
    """THE AMBER CEILING, ratified. project_hygiene never returns `down`, so this
    capability can never be red — deliberately. Manufacturing a red path for
    symmetry would put bookkeeping beside a dead scheduler."""
    _set(seeded, "project_hygiene", "degraded", "record_stale")
    assert _cap(evaluate(seeded), "project_tracking")["status"] == "amber"

    _set(seeded, "project_hygiene", "ok")
    assert _cap(evaluate(seeded), "project_tracking")["status"] == "ok"


def test_gated_capabilities_are_reported_not_counted(seeded):
    rollup = evaluate(seeded)
    assert _cap(rollup, "flight_booking")["status"] == "gated"
    assert _cap(rollup, "local_network")["status"] == "gated"

    s = summary(rollup)
    assert s["live_total"] == 8
    assert s["gated_total"] == 2


def test_a_gated_capability_goes_live_when_its_flag_turns_on(seeded, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "booking_enabled", True)
    _set(seeded, "duffel", "down", "call_failed")
    assert _cap(evaluate(seeded), "flight_booking")["status"] == "red"


# ── runbook resolution ───────────────────────────────────────────────────────

def test_runbook_comes_from_the_driving_member(seeded):
    """Never the capability's own generic text — the runbook that helps is the one
    belonging to the component actually responsible."""
    _set(seeded, "location_responsiveness", "down", "not_answering")
    loc = _cap(evaluate(seeded), "location")
    assert loc["status"] == "red"
    assert loc["driving_member"] == "location_responsiveness"
    assert "PHONE-SIDE" in loc["remediation"]["runbook"]


def test_a_missing_runbook_degrades_to_none_not_an_invention(seeded):
    _set(seeded, "twilio", "down", "no_such_fault_code")
    cap = _cap(evaluate(seeded), "voice_sms")
    assert cap["status"] == "red"
    assert cap["remediation"] is None


def test_the_live_calendar_fault_code_resolves_to_a_runbook(seeded):
    """check_liveness emits `call_failed`, but the only calendar runbook was keyed
    to `auth_invalid` — so a real, days-long calendar outage rendered with NO
    runbook at all. Regression pin."""
    from app.health import get_runbook

    assert get_runbook(seeded, "google_calendar_svcacct", "call_failed") is not None
    assert get_runbook(seeded, "google_oauth", "call_failed") is not None


# ── the meta-check ───────────────────────────────────────────────────────────

def test_coherence_is_clean_on_the_shipped_seed(seeded):
    assert coherence_problems(seeded) == []


def test_coherence_catches_a_member_that_is_not_a_component(seeded):
    seeded.add(CapabilityMember(capability="memory", component="ghost_component",
                                is_primary=False))
    seeded.commit()
    assert any("ghost_component" in p for p in coherence_problems(seeded))


def test_coherence_catches_a_disabled_member(seeded):
    seeded.get(Component, "twilio").enabled = False
    seeded.commit()
    assert any("twilio" in p for p in coherence_problems(seeded))


def test_coherence_catches_a_capability_with_no_primary(seeded):
    for m in seeded.query(CapabilityMember).filter(
            CapabilityMember.capability == "voice_sms").all():
        m.is_primary = False
    seeded.commit()
    assert any("no primary" in p for p in coherence_problems(seeded))


def test_evaluator_reports_stale_when_no_cycle_has_run(seeded):
    """The one check that must not be faked. With no heartbeat it is `unknown` —
    never `ok`, because 'nobody has reported' is not 'everything is fine'."""
    from app.health_checks import run_check

    r = run_check(seeded, seeded.get(Component, "health_evaluator"))
    assert r.status == "unknown"
    assert r.fault_code == "no_heartbeat"


def test_evaluator_goes_ok_after_a_cycle_and_down_when_stale(seeded):
    from datetime import datetime, timedelta, timezone

    from app.health_checks import run_check, run_health_cycle
    from app.models import EvaluatorHeartbeat

    run_health_cycle(seeded)
    c = seeded.get(Component, "health_evaluator")
    assert run_check(seeded, c).status == "ok"

    hb = seeded.get(EvaluatorHeartbeat, 1)
    hb.beat_at = datetime.now(timezone.utc) - timedelta(seconds=2000)
    seeded.commit()

    r = run_check(seeded, c)
    assert r.status == "down"
    assert r.fault_code == "evaluator_stale"


def test_status_page_does_not_stamp_the_evaluator_heartbeat(seeded):
    """THE TRAP. If viewing the status page refreshed the beat, opening the page
    would prove the evaluator alive by the act of asking — and a dead background
    evaluator would read green to the only person looking."""
    from app.health_checks import status_payload
    from app.models import EvaluatorHeartbeat

    status_payload(seeded)
    assert seeded.get(EvaluatorHeartbeat, 1) is None


# ── the brief line ───────────────────────────────────────────────────────────

def test_brief_line_speaks_when_all_green(seeded):
    """THE SCOPED EXCEPTION to exception-first: a monitor that only speaks on
    failure is indistinguishable from one that has stopped."""
    for m in seeded.query(CapabilityMember).all():
        _set(seeded, m.component, "ok")
    line = brief_line(seeded)
    assert line == "All 8 capabilities green."


def test_brief_line_names_reds_and_ambers_inline(seeded):
    for m in seeded.query(CapabilityMember).all():
        _set(seeded, m.component, "ok")
    _set(seeded, "google_calendar_svcacct", "down", "call_failed")

    line = brief_line(seeded)
    assert "Calendar red" in line          # primary of calendar
    assert "Morning brief amber" in line   # non-primary of the brief
    assert "capabilities green" in line


def test_brief_line_puts_reds_before_ambers(seeded):
    for m in seeded.query(CapabilityMember).all():
        _set(seeded, m.component, "ok")
    _set(seeded, "google_calendar_svcacct", "down", "call_failed")
    line = brief_line(seeded)
    assert line.index("Calendar red") < line.index("Morning brief amber")


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_capabilities_endpoint_requires_auth(client):
    assert client.get("/api/status/capabilities").status_code == 401


def test_capabilities_endpoint_returns_the_rollup(client, db, auth_headers):
    seed_health_topology(db)
    r = client.get("/api/status/capabilities", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()

    assert body["summary"]["live_total"] == 8
    assert body["summary"]["gated_total"] == 2
    names = {c["name"] for c in body["capabilities"]}
    assert "location" in names and "flight_booking" in names


def test_capabilities_endpoint_leaks_no_secrets(client, db, auth_headers):
    """`depends_on` names secret ENV VARS, so it must never reach a payload — the
    same rule /api/status/full is held to."""
    seed_health_topology(db)
    body = client.get("/api/status/capabilities", headers=auth_headers).json()
    blob = repr(body)
    assert "depends_on" not in blob
    for secretish in ("API_KEY", "AUTH_TOKEN", "REFRESH_TOKEN", "SERVICE_ACCOUNT"):
        assert secretish not in blob


# ── the fault-code -> runbook join ───────────────────────────────────────────
#
# THE BUG THIS PREVENTS. `check_liveness` emits exactly one fault code,
# `call_failed`, but the runbooks for calendar and oauth were keyed to
# `auth_invalid` / `token_expired` — codes no check produces. So they could never
# join, and a real four-day calendar outage rendered on the status page naming the
# fault and offering nothing. Eight more components had no runbook at all.
#
# A runbook that cannot join is worse than a missing one: it looks like coverage.
# The project arc is about to add checks and runbooks to this same join, so it is
# enforced here rather than re-audited by hand later.

# check_type -> the fault codes that check can emit as a real, actionable fault.
# `unknown`-tier codes (no_evidence / no_heartbeat / no_requests / no_projects) are
# deliberately excluded: they mean "no basis to judge", not "here is a fault to
# fix", and a runbook for them would be advice about an absence.
_EMITTABLE_FAULTS = {
    "liveness":                {"call_failed"},
    "heartbeat":               {"heartbeat_stale"},
    "location_scheduler":      {"not_asking", "relay_rejected"},
    "location_responsiveness": {"not_answering"},
    "project_hygiene":         {"record_stale"},
    "health_evaluator":        {"evaluator_stale", "rollup_incoherent"},
}


def _emittable_pairs():
    """(component, fault_code) pairs a check can actually produce.

    Honours `_APP_UP`, which OVERRIDES a component's declared check_type —
    `postgres` is declared `liveness` but is routed to `check_app_up`, so it can
    never emit `call_failed`. Reading check_type alone gets this wrong.
    """
    from app.health import _COMPONENTS
    from app.health_checks import _APP_UP, _CHECKS

    pairs = set()
    for spec in _COMPONENTS:
        name = spec["name"]
        ctype = spec.get("check_type", "none")
        if name in _APP_UP:
            pairs.add((name, "check_error"))      # an unreachable DB looks like this
            continue
        if ctype not in _CHECKS:
            continue
        for code in _EMITTABLE_FAULTS.get(ctype, ()):
            pairs.add((name, code))
    return pairs


def test_every_emittable_fault_has_a_runbook(seeded):
    """No component may go non-ok with nothing to say."""
    from app.health import get_runbook

    missing = [(c, f) for c, f in sorted(_emittable_pairs())
               if get_runbook(seeded, c, f) is None]
    assert missing == [], f"faults with no runbook: {missing}"


def test_no_runbook_is_keyed_to_a_fault_no_check_emits(seeded):
    """The other half, and the one that actually bit. A dead runbook reads as
    coverage on inspection and renders nothing in production."""
    from app.models import Remediation

    emittable = _emittable_pairs()
    stored = {(r.component, r.fault_code) for r in seeded.query(Remediation).all()}
    dead = sorted(stored - emittable)
    assert dead == [], f"runbooks that can never join: {dead}"


def test_seed_actually_deletes_a_retired_runbook(seeded):
    """Reconciling never deletes on its own — retirement has to be explicit or it
    does not happen. Pinned because the six dead runbooks were removed from the
    code list AND added to _RETIRED_REMEDIATIONS; only the second one clears rows
    from a database that already has them."""
    from app.models import Remediation

    seeded.add(Remediation(component="duffel", fault_code="401", runbook="stale", severity="warn"))
    seeded.commit()

    seed_health_topology(seeded)

    assert (seeded.query(Remediation)
            .filter(Remediation.component == "duffel", Remediation.fault_code == "401")
            .first()) is None


# ── audit starvation: the brief must FEED the checks that watch it ───────────
#
# THE BUG. `check_liveness` derives health from `actions_audit`. The morning brief
# read the calendar every single day through a direct handler call and wrote no
# audit row — so the one thing that exercised the component routinely was invisible
# to the one check that watched it. `google_calendar_svcacct` then latched `down`
# for a day on a fault that had already resolved, because nothing could clear it.
#
# A latched check is worse than a missing one: it reports a fault with total
# confidence, and the fault is gone.

def test_the_brief_records_audit_rows_for_its_tool_backed_sources(db, monkeypatch):
    """The starvation fix. Whatever the sources return, the brief must leave
    evidence that it ran them."""
    from app import briefing
    from app.health import component_for_tool
    from app.models import ActionAudit

    monkeypatch.setattr(briefing, "_nws_weather", lambda a: "")
    monkeypatch.setattr(briefing, "_nws_marine", lambda: "")
    monkeypatch.setattr(briefing, "_news_brief", lambda: "")

    briefing.gather_context(db)

    tools = {r.tool for r in db.query(ActionAudit).all()}
    assert "briefing:calendar_lookup" in tools, tools

    # and the rows must RESOLVE to the component whose liveness reads them —
    # an audit row nothing maps to feeds nothing.
    assert component_for_tool("briefing:calendar_lookup") == "google_calendar_svcacct"


def test_brief_audit_status_is_derived_not_asserted(db, monkeypatch):
    """A row that says `ok` because the caller assumed so is the fabricated-`ok`
    bug that made 539 pre-epoch rows worthless. A failing source must record
    `error`."""
    from app import briefing
    from app.handlers.base import ToolFault
    from app.models import ActionAudit

    monkeypatch.setattr(briefing, "_nws_weather", lambda a: "")
    monkeypatch.setattr(briefing, "_nws_marine", lambda: "")
    monkeypatch.setattr(briefing, "_news_brief", lambda: "")

    def _boom(args, ctx):
        raise ToolFault("Error reading calendar: invalid_grant")

    monkeypatch.setattr("app.handlers.scheduling._calendar_lookup", _boom)

    briefing.gather_context(db)

    rows = [r for r in db.query(ActionAudit).all() if r.tool == "briefing:calendar_lookup"]
    assert rows, "no audit row recorded for the failing source"
    assert all(r.status == "error" for r in rows), [r.status for r in rows]


def test_a_failing_source_is_still_kept_out_of_the_brief(db, monkeypatch):
    """The audited seam must not cost the PR #44 property: a failed source is
    dropped, never narrated. `run_tool`'s raw error string does not start with
    '(', so `_safe_tool` restores that shape deliberately."""
    from app import briefing
    from app.handlers.base import ToolFault

    monkeypatch.setattr(briefing, "_nws_weather", lambda a: "")
    monkeypatch.setattr(briefing, "_nws_marine", lambda: "")
    monkeypatch.setattr(briefing, "_news_brief", lambda: "")
    monkeypatch.setattr("app.handlers.infra._fleet_health",
                        lambda a, c: (_ for _ in ()).throw(ToolFault("fly exploded")))

    ctx = briefing.gather_context(db)
    assert "fly exploded" not in ctx
    assert "## Hosted apps" not in ctx


def test_no_new_direct_handler_bypasses_are_introduced(db):
    """THE GUARD. The latch happened because one routine call went around the
    audited seam. Any tool exercised outside the registry is a latent latch of the
    same shape, so new ones must be deliberate rather than accidental.

    Allow-listed exceptions are tools whose component carries no liveness check,
    so no check can starve on them.
    """
    import io
    import os
    import re

    from app.handlers.base import build_registry
    from app.health import component_for_tool
    from app.health_checks import _APP_UP

    reg = build_registry(include_delegate=False)
    fn_for_tool = {}
    for t in sorted(reg._tools):
        n = getattr(reg._tools[t].fn, "__name__", "")
        if n:
            fn_for_tool.setdefault(n, t)

    liveness = set()
    from app.health import _COMPONENTS
    for spec in _COMPONENTS:
        if spec.get("check_type") == "liveness" and spec["name"] not in _APP_UP:
            liveness.add(spec["name"])

    offenders = []
    for root, _, files in os.walk("app"):
        if "handlers" in root:              # handler-internal calls are not bypasses
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f).replace("\\", "/")
            src = io.open(path, encoding="utf-8", errors="replace").read()
            for fname, tool in fn_for_tool.items():
                if component_for_tool(tool) not in liveness:
                    continue
                for m in re.finditer(r"(?<!def )\b%s\s*\(" % re.escape(fname), src):
                    line = src[:m.start()].count("\n") + 1
                    text = src.splitlines()[line - 1].strip()
                    if text.startswith(("def ", "#")):
                        continue
                    offenders.append(f"{path}:{line} calls {fname}() [tool={tool}]")

    assert offenders == [], (
        "direct handler calls bypass Registry.run_tool, so they write no audit row "
        "and can starve a liveness check into latching:\n  " + "\n  ".join(offenders))
