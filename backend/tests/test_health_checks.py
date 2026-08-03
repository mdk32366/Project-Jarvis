"""Health checks (health TDD §5, roadmap R5).

The load-bearing test is the NEGATIVE path (build §0.3): prove liveness can turn
red on real failure, since production is otherwise all-green and would give a
health system no evidence it can detect anything. PR-0 made `actions_audit` record
faults; here we feed synthetic failure rows and assert `down`.
"""

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.health import seed_health_topology
from app.health_checks import (
    check_app_up, check_heartbeat, check_liveness, check_location_responsiveness,
    check_location_scheduler, run_all_checks, run_check,
)
from app.models import (
    ActionAudit, Component, HealthResult, LocationRequest, SchedulerHeartbeat,
)


def _now():
    return datetime.now(timezone.utc)


def _audit(db, tool, status, when=None):
    db.add(ActionAudit(channel="web", actor="t", tool=tool, arguments="{}",
                       result="", status=status, created_at=when or _now()))
    db.commit()


def _c(db, name):
    return db.get(Component, name)


# ── liveness (§5.1) ──────────────────────────────────────────────────────────

def test_liveness_unknown_with_no_evidence(db):
    seed_health_topology(db)
    r = check_liveness(db, _c(db, "duffel"))
    assert r.status == "unknown"                      # absence of failure is NOT health


def test_liveness_down_on_recent_failure(db):
    """THE negative path: a real failure must render red (build §0.3)."""
    seed_health_topology(db)
    _audit(db, "search_flights", "error")
    r = check_liveness(db, _c(db, "duffel"))
    assert r.status == "down" and r.fault_code == "call_failed"
    assert r.last_failure_at is not None


def test_liveness_ok_when_latest_succeeds(db):
    seed_health_topology(db)
    # Recent offsets: always post-epoch AND inside the 30-day window, so the test
    # stays stable regardless of wall-clock.
    _audit(db, "search_flights", "error", when=_now() - timedelta(minutes=10))
    _audit(db, "search_flights", "ok", when=_now() - timedelta(minutes=1))  # recovered
    r = check_liveness(db, _c(db, "duffel"))
    assert r.status == "ok"
    assert r.last_success_at and r.last_failure_at    # both timestamps captured


def test_liveness_confirmed_and_refused_map_to_ok(db):
    """A confirmed send and a refused gated call are the safety machinery WORKING
    — they must map to ok, never fault (PR-0 / build §0.3)."""
    seed_health_topology(db)
    _audit(db, "send_email", "confirmed")
    _audit(db, "book_flight", "refused")
    assert check_liveness(db, _c(db, "gmail")).status == "ok"
    assert check_liveness(db, _c(db, "duffel")).status == "ok"


def test_liveness_resolves_agent_prefixed_rows(db):
    seed_health_topology(db)
    _audit(db, "researcher:web_search", "error")      # sub-agent audit row
    assert check_liveness(db, _c(db, "tavily")).status == "down"


def test_liveness_ignores_pre_epoch_rows(db):
    """Rows before the PR-0 audit-truthful epoch are 'ok' by construction and are
    NOT evidence — a component seen ONLY before the epoch is unknown, not falsely
    green (17 days of fabricated history must not dilute the signal)."""
    from app.health_checks import _AUDIT_TRUTHFUL_EPOCH
    seed_health_topology(db)
    _audit(db, "search_flights", "ok", when=_AUDIT_TRUTHFUL_EPOCH - timedelta(days=1))
    assert check_liveness(db, _c(db, "duffel")).status == "unknown"   # pre-epoch: no evidence
    _audit(db, "search_flights", "ok", when=_now() - timedelta(minutes=1))
    assert check_liveness(db, _c(db, "duffel")).status == "ok"        # recent: real evidence


# ── heartbeat (§5.2) ─────────────────────────────────────────────────────────

def test_heartbeat_unknown_when_absent(db):
    seed_health_topology(db)
    assert check_heartbeat(db, _c(db, "worker_scheduler")).status == "unknown"


def test_heartbeat_down_when_stale(db):
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now() - timedelta(seconds=400), enabled=True))
    db.commit()
    r = check_heartbeat(db, _c(db, "worker_scheduler"))
    assert r.status == "down" and r.fault_code == "heartbeat_stale"   # 400 > seeded 300


def test_heartbeat_ok_when_fresh(db):
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now(), enabled=True))
    db.commit()
    assert check_heartbeat(db, _c(db, "worker_scheduler")).status == "ok"


def test_heartbeat_disabled_is_ok_not_down(db):
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now() - timedelta(hours=5), enabled=False))
    db.commit()
    r = check_heartbeat(db, _c(db, "worker_scheduler"))
    assert r.status == "ok" and "disabled" in r.detail                # disabled != down


# ── the location split (TDD location-pull §7) ────────────────────────────────
#
# The point of these two checks is ATTRIBUTION: the same visible symptom ("no
# fresh position") must resolve to a different component depending on whether the
# server stopped asking or the phone stopped answering. The retired single
# freshness check could not tell those apart.

def _always_active(monkeypatch):
    monkeypatch.setattr(settings, "location_active_start_hour", 0)
    monkeypatch.setattr(settings, "location_active_end_hour", 24)


def _req(db, *, status="fulfilled", trigger="scheduled", age_min=0.0,
         relay_accepted=True, error=""):
    r = LocationRequest(
        nonce=f"n{db.query(LocationRequest).count()}-{status}-{age_min}",
        trigger=trigger, status=status, relay_accepted=relay_accepted, relay_error=error,
        requested_at=_now() - timedelta(minutes=age_min),
        responded_at=_now() - timedelta(minutes=age_min) if status == "fulfilled" else None,
    )
    db.add(r)
    db.commit()
    return r


def test_scheduler_unknown_when_never_asked(db):
    """No evidence is not health — a fresh deploy has no basis for a green."""
    seed_health_topology(db)
    r = check_location_scheduler(db, _c(db, "location_pull_scheduler"))
    assert r.status == "unknown" and r.fault_code == "no_requests"


def test_scheduler_ok_when_asking_on_time(db, monkeypatch):
    seed_health_topology(db)
    _always_active(monkeypatch)
    _req(db, age_min=5)                                    # interval 15 -> ok <= 20
    assert check_location_scheduler(db, _c(db, "location_pull_scheduler")).status == "ok"


def test_scheduler_down_when_not_asking(db, monkeypatch):
    seed_health_topology(db)
    _always_active(monkeypatch)
    _req(db, age_min=45)                                   # > interval*2 (30)
    r = check_location_scheduler(db, _c(db, "location_pull_scheduler"))
    assert r.status == "down" and r.fault_code == "not_asking"


def test_scheduler_ok_outside_active_hours(db, monkeypatch):
    """An overnight gap is not a fault — nothing is asking because nothing should."""
    seed_health_topology(db)
    monkeypatch.setattr(settings, "location_active_start_hour", 3)
    monkeypatch.setattr(settings, "location_active_end_hour", 3)   # empty -> always outside
    _req(db, age_min=600)
    r = check_location_scheduler(db, _c(db, "location_pull_scheduler"))
    assert r.status == "ok" and "outside active hours" in r.detail


def test_scheduler_relay_rejection_is_its_own_fault_code(db, monkeypatch):
    """Minting requests the relay then refuses is a DIFFERENT fault from not
    minting them, and it sends you to the key rather than the worker."""
    seed_health_topology(db)
    _always_active(monkeypatch)
    _req(db, age_min=1, relay_accepted=False, error="relay rejected: NotRegistered")
    r = check_location_scheduler(db, _c(db, "location_pull_scheduler"))
    assert r.status == "down" and r.fault_code == "relay_rejected"


def test_retired_dispatch_failing_runbook_is_removed(db):
    """A renamed fault code must not leave its old runbook joinable — that runbook
    sent the reader somewhere the fault no longer lives."""
    from app.models import Remediation

    seed_health_topology(db)
    db.add(Remediation(component="location_pull_scheduler", fault_code="dispatch_failing",
                       runbook="stale", severity="warn"))
    db.commit()
    seed_health_topology(db)
    assert db.query(Remediation).filter(
        Remediation.component == "location_pull_scheduler",
        Remediation.fault_code == "dispatch_failing").first() is None
    assert db.query(Remediation).filter(
        Remediation.component == "location_pull_scheduler",
        Remediation.fault_code == "relay_rejected").first() is not None


def test_scheduler_ignores_on_demand_requests(db, monkeypatch):
    """An on-demand pull is not evidence the SCHEDULE is running."""
    seed_health_topology(db)
    _always_active(monkeypatch)
    _req(db, trigger="on_demand", age_min=1)
    assert check_location_scheduler(db, _c(db, "location_pull_scheduler")).status == "unknown"


def test_responsiveness_unknown_below_evidence_floor(db):
    """Two answered requests is not enough to call the phone healthy."""
    seed_health_topology(db)
    _req(db, status="fulfilled")
    _req(db, status="fulfilled")
    r = check_location_responsiveness(db, _c(db, "location_responsiveness"))
    assert r.status == "unknown" and r.fault_code == "no_evidence"


def test_responsiveness_ok_degraded_down_thresholds(db):
    seed_health_topology(db)
    for _ in range(6):
        _req(db, status="fulfilled")
    assert check_location_responsiveness(db, _c(db, "location_responsiveness")).status == "ok"

    for _ in range(2):                                     # window slides: 4 of last 6
        _req(db, status="timeout")
    assert check_location_responsiveness(db, _c(db, "location_responsiveness")).status == "degraded"

    for _ in range(4):                                     # 0 of last 6
        _req(db, status="timeout")
    r = check_location_responsiveness(db, _c(db, "location_responsiveness"))
    assert r.status == "down" and r.fault_code == "not_answering"


def test_responsiveness_ignores_pending(db):
    """A pull in flight has not failed yet; counting it would read red on every tick."""
    seed_health_topology(db)
    for _ in range(6):
        _req(db, status="fulfilled")
    for _ in range(6):
        _req(db, status="pending")
    assert check_location_responsiveness(db, _c(db, "location_responsiveness")).status == "ok"


def test_old_location_pings_component_is_retired(db):
    """Retirement must actually delete: the seed reconciles fields but does not
    remove rows, so a dropped component would otherwise keep running its old check."""
    seed_health_topology(db)
    db.add(Component(name="location_pings", kind="data_feed", check_type="freshness"))
    db.add(HealthResult(component="location_pings", status="down"))
    db.commit()
    seed_health_topology(db)
    assert db.get(Component, "location_pings") is None
    assert db.get(HealthResult, "location_pings") is None


# ── never-raise (§4.4) ───────────────────────────────────────────────────────

def test_check_never_raises_into_caller(db, monkeypatch):
    seed_health_topology(db)
    import app.health_checks as hc
    monkeypatch.setitem(hc._CHECKS, "liveness",
                        lambda db, c: (_ for _ in ()).throw(RuntimeError("boom")))
    r = run_check(db, _c(db, "duffel"))              # duffel is liveness
    assert r.status == "unknown" and r.fault_code == "check_error"


# ── run_all_checks: upsert + transient ───────────────────────────────────────

def test_run_all_upserts_and_is_transient(db):
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now(), enabled=True))
    db.commit()
    run_all_checks(db)
    assert db.get(HealthResult, "worker_scheduler").status == "ok"
    # a second run overwrites — one row per component, not appended history
    run_all_checks(db)
    assert db.query(HealthResult).filter(HealthResult.component == "worker_scheduler").count() == 1


def test_app_up_ok(db):
    seed_health_topology(db)
    assert check_app_up(db, _c(db, "postgres")).status == "ok"


# ══ Location freshness (build order steps 1, 4, 4a) ═════════════════════════
from datetime import date as _date  # noqa: E402


def _freshness_component(db):
    from app.health import seed_health_topology
    from app.models import Component
    seed_health_topology(db)
    return db.query(Component).filter_by(name="location_freshness").one()


def _ping(db, *, minutes_ago: int):
    from app.models import LocationPing
    p = LocationPing(lat=48.49, lon=-122.68, source="test", trigger="pull")
    db.add(p)
    db.commit()
    p.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    db.commit()
    return p


def test_freshness_never_pinged_is_unknown_not_down(db):
    """No evidence is not health — and a phone never enrolled is not a phone that
    stopped, so this is `unknown`, never `down`."""
    from app.health_checks import check_location_freshness
    r = check_location_freshness(db, _freshness_component(db))
    assert r.status == "unknown" and r.fault_code == "never_pinged"


def test_freshness_fresh_in_hours_is_ok(db, monkeypatch):
    from app.health_checks import check_location_freshness
    monkeypatch.setattr("app.handlers.location.in_active_hours", lambda db, now=None: True)
    _ping(db, minutes_ago=5)
    r = check_location_freshness(db, _freshness_component(db))
    assert r.status == "ok" and r.fault_code is None
    assert "5m old" in r.detail


def test_freshness_stale_in_hours_is_degraded(db, monkeypatch):
    from app.health_checks import check_location_freshness
    monkeypatch.setattr("app.handlers.location.in_active_hours", lambda db, now=None: True)
    _ping(db, minutes_ago=45)          # > 30, <= 60
    r = check_location_freshness(db, _freshness_component(db))
    assert r.status == "degraded" and r.fault_code == "stale_during_active"


def test_freshness_very_stale_in_hours_is_down(db, monkeypatch):
    from app.health_checks import check_location_freshness
    monkeypatch.setattr("app.handlers.location.in_active_hours", lambda db, now=None: True)
    _ping(db, minutes_ago=180)         # > 60
    r = check_location_freshness(db, _freshness_component(db))
    assert r.status == "down" and r.fault_code == "stale_during_active"


def test_freshness_stale_OUT_of_hours_is_ok_but_still_states_the_age(db, monkeypatch):
    """A phone on a charger overnight is not a fault, and reporting one nightly is
    how a panel gets ignored. REPORT ALWAYS, ESCALATE ONLY IN-HOURS — the age is
    still in the detail, only the escalation is withheld.

    This is the test the required plant targets: delete the in_active_hours branch
    and it must go RED. If it stays green the fixture was in-hours by accident and
    the suppression was never exercised.
    """
    from app.health_checks import check_location_freshness
    monkeypatch.setattr("app.handlers.location.in_active_hours", lambda db, now=None: False)
    _ping(db, minutes_ago=600)         # wildly stale
    r = check_location_freshness(db, _freshness_component(db))
    assert r.status == "ok", "an overnight gap escalated"
    assert r.fault_code is None
    assert "600m old" in r.detail, "the age must still be stated as fact"
    assert "outside active hours" in r.detail


# ── Step 4: the answering_late split ────────────────────────────────────────
def _resp_component(db):
    from app.health import seed_health_topology
    from app.models import Component
    seed_health_topology(db)
    return db.query(Component).filter_by(name="location_responsiveness").one()


def _request(db, *, status, late_minutes=None, i=[0]):
    from app.models import LocationRequest
    i[0] += 1
    now = datetime.now(timezone.utc)
    r = LocationRequest(nonce=f"n{i[0]:022d}", trigger="scheduled", status=status,
                        relay_accepted=True)
    db.add(r)
    db.commit()
    r.requested_at = now - timedelta(minutes=120)
    if status == "fulfilled":
        r.responded_at = r.requested_at + timedelta(seconds=30)
    elif late_minutes is not None:
        r.responded_at = r.requested_at + timedelta(minutes=late_minutes)
    db.commit()
    return r


def test_all_silent_failures_report_not_answering(db):
    from app.health_checks import check_location_responsiveness
    for _ in range(6):
        _request(db, status="timeout")
    r = check_location_responsiveness(db, _resp_component(db))
    assert r.fault_code == "not_answering"
    assert "6 failures silent" in r.detail


def test_majority_late_failures_report_answering_late(db):
    """Something answered, so the Tasker config is demonstrably fine — this must
    route to the power-management runbook, not the config one."""
    from app.health_checks import check_location_responsiveness
    for _ in range(4):
        _request(db, status="timeout", late_minutes=47)
    for _ in range(2):
        _request(db, status="timeout")
    r = check_location_responsiveness(db, _resp_component(db))
    assert r.fault_code == "answering_late"
    assert "4 of 6 failures answered late" in r.detail
    assert "median 47m" in r.detail and "timeout 120s" in r.detail


def test_mixed_with_silent_in_the_majority_stays_not_answering(db):
    from app.health_checks import check_location_responsiveness
    for _ in range(2):
        _request(db, status="timeout", late_minutes=30)
    for _ in range(4):
        _request(db, status="timeout")
    r = check_location_responsiveness(db, _resp_component(db))
    assert r.fault_code == "not_answering"


def test_a_late_answer_does_not_change_the_status_tier(db):
    """Ratified when close_request was written and NOT reopened here: a phone
    answering 50 minutes late against a 120-second timeout is still unresponsive.
    Only the fault code and the detail change."""
    from app.health_checks import check_location_responsiveness
    for _ in range(6):
        _request(db, status="timeout", late_minutes=50)
    r = check_location_responsiveness(db, _resp_component(db))
    assert r.status == "down", "late answers must not soften the tier"
    assert r.fault_code == "answering_late"


def test_fulfilment_is_counted_from_status_never_from_responded_at(db):
    """THE 4a.1 GUARD, and the reason it exists.

    `responded_at` is now stamped on LATE closes too, so `responded_at is not
    None` reads like a perfectly reasonable way to count "answered" — and would
    count every late answer as on-time, turning a correctly-red check green
    without a single fix arriving sooner. That is the fabricated-green move
    design-note-answering-late.md warns against, one refactor away.

    Structural, because a behavioural test alone would not stop the next person
    writing the tempting filter.
    """
    import inspect
    from app import health_checks

    src = inspect.getsource(health_checks.check_location_responsiveness)
    fulfil_line = [l for l in src.splitlines() if "fulfilled = [" in l]
    assert fulfil_line, "could not find the fulfilment filter — guard is not reaching it"
    assert 'r.status == "fulfilled"' in fulfil_line[0], \
        "fulfilment must be counted from status"
    assert "responded_at" not in fulfil_line[0], \
        "fulfilment is being counted from responded_at — late answers would read on-time"

    # And behaviourally: rows that answered late must NOT count as fulfilled.
    from app.health_checks import check_location_responsiveness
    for _ in range(6):
        _request(db, status="timeout", late_minutes=45)
    r = check_location_responsiveness(db, _resp_component(db))
    assert "0 of the last 6 requests answered" in r.detail
