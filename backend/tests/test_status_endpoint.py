"""GET /api/status/full — the single status payload (TDD §8.2, roadmap PR-D).

Runs every check fresh, joins the stored runbook + the evidence rows for anything
not-ok, and carries no secrets. Auth-gated.
"""

from datetime import datetime, timedelta, timezone

from app.health import seed_health_topology
from app.health_checks import status_payload
from app.models import ActionAudit, SchedulerHeartbeat


def _now():
    return datetime.now(timezone.utc)


def _audit(db, tool, status, result="Flight search failed (422)", when=None):
    db.add(ActionAudit(channel="web", actor="t", tool=tool, arguments='{"origin":"XXX"}',
                       result=result, status=status, created_at=when or _now()))
    db.commit()


def test_payload_shape(db):
    seed_health_topology(db)
    p = status_payload(db)
    assert set(p) == {"generated_at", "summary", "checks"}
    assert set(p["summary"]) >= {"ok", "degraded", "down", "unknown"}
    assert p["checks"] and all("component" in c and "status" in c for c in p["checks"])


def test_ok_component_omits_remediation_and_evidence(db):
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now(), enabled=True))
    db.commit()
    p = status_payload(db)
    wc = next(c for c in p["checks"] if c["component"] == "worker_scheduler")
    assert wc["status"] == "ok"
    assert "remediation" not in wc and "evidence" not in wc   # nothing to show when green


def test_not_ok_joins_stored_runbook(db):
    """A heartbeat-stale fault joins to its seeded runbook (never improvised)."""
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now() - timedelta(seconds=400), enabled=True))
    db.commit()
    p = status_payload(db)
    wc = next(c for c in p["checks"] if c["component"] == "worker_scheduler")
    assert wc["status"] == "down"
    assert wc["remediation"] and "fly apps restart" in wc["remediation"]["runbook"]


def test_liveness_down_carries_evidence(db):
    seed_health_topology(db)
    _audit(db, "search_flights", "error")
    p = status_payload(db)
    d = next(c for c in p["checks"] if c["component"] == "duffel")
    assert d["status"] == "down"
    assert d["evidence"] and d["evidence"][0]["tool"] == "search_flights"
    assert d["evidence"][0]["status"] == "error"


def test_a_failing_component_now_carries_its_runbook(db):
    """THE FIX. This test previously asserted `remediation is None` for a failing
    duffel and called that graceful — it was pinning the BUG as intended
    behaviour. `check_liveness` emits `call_failed`; nothing was keyed to it, so
    eight components could go down showing a fault and no guidance. That is what a
    real four-day calendar outage looked like on this page."""
    seed_health_topology(db)
    _audit(db, "search_flights", "error")
    d = next(c for c in status_payload(db)["checks"] if c["component"] == "duffel")
    assert d["fault_code"] == "call_failed"
    assert d["remediation"] is not None
    assert "DUFFEL_API_KEY" in d["remediation"]["runbook"]


def test_missing_runbook_degrades_gracefully(db):
    """The graceful path is still real and still worth pinning — it just has to be
    CONSTRUCTED now rather than found lying around. A runbook deleted at runtime
    (or a check emitting a code nobody has written up yet) yields remediation=None
    and still shows evidence, never a crash."""
    from app.models import Remediation

    seed_health_topology(db)
    db.query(Remediation).filter(Remediation.component == "duffel",
                                 Remediation.fault_code == "call_failed").delete()
    db.commit()

    _audit(db, "search_flights", "error")
    d = next(c for c in status_payload(db)["checks"] if c["component"] == "duffel")
    assert d["fault_code"] == "call_failed"
    assert d["remediation"] is None
    assert d["evidence"]                 # graceful: no runbook, still shows evidence


def test_payload_is_not_a_secret_surface(db):
    seed_health_topology(db)
    _audit(db, "search_flights", "error")
    p = status_payload(db)
    d = next(c for c in p["checks"] if c["component"] == "duffel")
    # evidence shows WHAT failed, never the tool ARGUMENTS (which can hold PII)
    assert set(d["evidence"][0]) == {"tool", "status", "detail", "at"}
    # no component exposes depends_on (which names secret env vars)
    assert all("depends_on" not in c for c in p["checks"])


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_status_full_requires_auth(client):
    assert client.get("/api/status/full").status_code == 401


def test_status_full_returns_payload(client, auth_headers):
    r = client.get("/api/status/full", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "checks" in body and "summary" in body
