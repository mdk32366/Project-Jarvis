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
    check_app_up, check_freshness, check_heartbeat, check_liveness, run_all_checks, run_check,
)
from app.models import ActionAudit, Component, HealthResult, LocationPing, SchedulerHeartbeat


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
    _audit(db, "search_flights", "error", when=_now() - timedelta(hours=2))
    _audit(db, "search_flights", "ok", when=_now())   # recovered since the failure
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


# ── freshness (§5.3) + active-hours window ──────────────────────────────────

def test_freshness_degraded_when_stale_in_active_hours(db, monkeypatch):
    seed_health_topology(db)
    monkeypatch.setattr(settings, "location_active_start_hour", 0)
    monkeypatch.setattr(settings, "location_active_end_hour", 24)     # always active
    db.add(LocationPing(lat=1, lon=1, created_at=_now() - timedelta(minutes=40)))
    db.commit()
    assert check_freshness(db, _c(db, "location_pings")).status == "degraded"   # 20<40<=60


def test_freshness_down_when_very_stale(db, monkeypatch):
    seed_health_topology(db)
    monkeypatch.setattr(settings, "location_active_start_hour", 0)
    monkeypatch.setattr(settings, "location_active_end_hour", 24)
    db.add(LocationPing(lat=1, lon=1, created_at=_now() - timedelta(hours=3)))
    db.commit()
    assert check_freshness(db, _c(db, "location_pings")).status == "down"


def test_freshness_suppressed_outside_active_hours(db, monkeypatch):
    seed_health_topology(db)
    monkeypatch.setattr(settings, "location_active_start_hour", 3)
    monkeypatch.setattr(settings, "location_active_end_hour", 3)      # empty window -> always outside
    db.add(LocationPing(lat=1, lon=1, created_at=_now() - timedelta(hours=6)))
    db.commit()
    r = check_freshness(db, _c(db, "location_pings"))
    assert r.status == "ok" and "outside active hours" in r.detail


def test_freshness_unknown_with_no_pings(db):
    seed_health_topology(db)
    assert check_freshness(db, _c(db, "location_pings")).status == "unknown"


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
