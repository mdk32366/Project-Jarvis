"""Request log — coarse per-request receipt + retention (health TDD §9 Phase 2).

The load-bearing test is #27: a crashed request must still leave a row, recorded
as `error`. The receipt is written on an independent session so it survives even
when the request's own transaction is broken by the crash.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import request_log
from app.config import settings
from app.models import RequestLog


def _now():
    return datetime.now(timezone.utc)


# ── receipt lifecycle ────────────────────────────────────────────────────────

def test_start_writes_in_progress_receipt(db):
    rid = request_log.start("web", "t", "me", "book me a flight")
    db.expire_all()
    row = db.get(RequestLog, rid)
    assert row.disposition == "in_progress"
    assert row.trigger == "book me a flight"


def test_finish_resolves_the_receipt(db):
    rid = request_log.start("web", "t", "me", "hi")
    request_log.finish(rid, "ok", 123)
    db.expire_all()
    row = db.get(RequestLog, rid)
    assert row.disposition == "ok" and row.duration_ms == 123


# ── run() wrapper: ok vs crashed (TDD #27) ───────────────────────────────────

def test_run_logs_ok_request(db, monkeypatch):
    import app.orchestrator as orch
    monkeypatch.setattr(orch, "_run_inner", lambda *a, **k: "reply")
    assert orch.run(db, "web", "t", "hello", "me") == "reply"
    db.expire_all()
    row = db.query(RequestLog).order_by(RequestLog.id.desc()).first()
    assert row.disposition == "ok" and row.trigger == "hello"
    assert row.duration_ms is not None


def test_run_logs_crashed_request_as_error(db, monkeypatch):
    import app.orchestrator as orch

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(orch, "_run_inner", boom)
    with pytest.raises(RuntimeError):
        orch.run(db, "web", "t", "do X", "me")
    db.expire_all()
    row = db.query(RequestLog).order_by(RequestLog.id.desc()).first()
    assert row.disposition == "error" and "kaboom" in row.error_detail   # #27


# ── retention: time primary, row-count valve ─────────────────────────────────

def test_prune_is_time_based(db, monkeypatch):
    monkeypatch.setattr(settings, "request_log_retention_days", 90)
    old = RequestLog(channel="web", trigger="old", disposition="ok",
                     received_at=_now() - timedelta(days=100))
    fresh = RequestLog(channel="web", trigger="fresh", disposition="ok",
                       received_at=_now() - timedelta(days=10))
    db.add_all([old, fresh])
    db.commit()
    request_log.prune(db)
    remaining = {r.trigger for r in db.query(RequestLog).all()}
    assert remaining == {"fresh"}          # the 100-day-old row swept, the 10-day kept


def test_row_count_safety_valve(db, monkeypatch):
    """A runaway loop can't fill the disk before the time sweep: the cap drops the
    oldest beyond max_rows even if all rows are recent."""
    monkeypatch.setattr(settings, "request_log_retention_days", 90)
    monkeypatch.setattr(settings, "request_log_max_rows", 5)
    for i in range(12):
        db.add(RequestLog(channel="web", trigger=f"r{i}", disposition="ok",
                          received_at=_now() - timedelta(seconds=12 - i)))
    db.commit()
    request_log.prune(db)
    assert db.query(RequestLog).count() == 5          # newest 5 kept
    kept = {r.trigger for r in db.query(RequestLog).all()}
    assert "r11" in kept and "r0" not in kept


# ── rollup ───────────────────────────────────────────────────────────────────

def test_recent_summary_counts_by_disposition(db):
    db.add(RequestLog(channel="web", trigger="a", disposition="ok"))
    db.add(RequestLog(channel="web", trigger="b", disposition="error", error_detail="boom"))
    db.commit()
    rs = request_log.recent_summary(db)
    assert rs["by_disposition"]["ok"] == 1
    assert rs["by_disposition"]["error"] == 1
    assert rs["recent_errors"] and rs["recent_errors"][0]["trigger"] == "b"
