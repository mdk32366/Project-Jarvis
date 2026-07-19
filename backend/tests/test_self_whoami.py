"""self_whoami + provenance (health TDD §9 Phase 1).

JARVIS can answer "what am I running, and am I healthy?" in conversation, reading
the SAME check state the /status page shows — one source, two renderers.
"""

from datetime import datetime, timezone

from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
from app.handlers.base import Context, build_registry
from app.health import seed_health_topology
from app.models import ActionAudit, SchedulerHeartbeat
from app.provenance import provenance


def _ctx(db):
    return Context(db=db, channel="web", actor="me", thread_key="t")


def _now():
    return datetime.now(timezone.utc)


def test_provenance_shape(db):
    p = provenance(db)
    assert set(p) >= {"commit", "build_time", "app", "region", "machine",
                      "version", "image", "in_service_days"}


def test_provenance_reads_baked_commit(db, monkeypatch):
    monkeypatch.setenv("APP_COMMIT", "abc123def4567890")
    assert provenance(db)["commit"] == "abc123def456"   # truncated to 12


def test_self_whoami_is_universal_and_ungated(db):
    # registered in BOTH branches like get_current_datetime
    top = build_registry(include_delegate=True)
    sub = build_registry()
    assert top.has("self_whoami") and not top.is_gated("self_whoami")
    assert sub.has("self_whoami")


def test_self_whoami_in_voice_allowlist():
    assert "self_whoami" in VOICE_TOOLS_PHASE1


def test_self_whoami_reports_provenance_and_health(db, monkeypatch):
    monkeypatch.setenv("APP_COMMIT", "deadbeefcafe0000")
    seed_health_topology(db)
    db.add(SchedulerHeartbeat(id=1, beat_at=_now(), enabled=True))
    db.commit()
    out = build_registry(include_delegate=True).execute("self_whoami", {}, _ctx(db))
    assert "commit deadbeefcafe" in out
    assert "Health:" in out and "OK" in out


def test_self_whoami_surfaces_a_down_component(db):
    """'How are you feeling' returns DETAIL, not a summary adjective — a real
    down component and its reason show up."""
    seed_health_topology(db)
    db.add(ActionAudit(channel="web", actor="t", tool="search_flights",
                       arguments="{}", result="fail", status="error", created_at=_now()))
    db.commit()
    out = build_registry(include_delegate=True).execute("self_whoami", {}, _ctx(db))
    assert "duffel" in out and "DOWN" in out
