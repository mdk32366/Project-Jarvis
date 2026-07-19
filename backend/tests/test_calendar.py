from datetime import datetime

import pytest

from app.config import settings
from app.handlers import scheduling
from app.handlers.base import Context, ToolFault


def test_calendar_unconfigured(db, monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", "")
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = scheduling._calendar_lookup({"range": "today"}, ctx)
    assert "not configured" in out.lower()


def test_time_window_ranges():
    t_start, t_end, label = scheduling._time_window("today")
    assert t_start < t_end and label == "today"
    tm_start, _, tm_label = scheduling._time_window("tomorrow")
    assert tm_start >= t_end and tm_label == "tomorrow"
    _, _, wlabel = scheduling._time_window("this week")
    assert wlabel == "this week"


def test_calendar_formats_events(db, monkeypatch):
    monkeypatch.setattr(scheduling, "_service", lambda: object())  # pretend configured
    sample = [
        {"summary": "Standup", "start": {"dateTime": "2026-07-02T09:00:00-07:00"}, "location": "Zoom"},
        {"summary": "Dentist", "start": {"date": "2026-07-02"}},
    ]
    monkeypatch.setattr(scheduling, "_fetch_events", lambda svc, cal, s, e: sample)
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = scheduling._calendar_lookup({"range": "this week"}, ctx)
    assert "Standup" in out and "Zoom" in out
    assert "Dentist" in out and "all day" in out


def test_calendar_no_events(db, monkeypatch):
    monkeypatch.setattr(scheduling, "_service", lambda: object())
    monkeypatch.setattr(scheduling, "_fetch_events", lambda svc, cal, s, e: [])
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = scheduling._calendar_lookup({"range": "today"}, ctx)
    assert "No events" in out


def test_calendar_error_is_caught(db, monkeypatch):
    monkeypatch.setattr(scheduling, "_service", lambda: object())
    def boom(*a): raise RuntimeError("api down")
    monkeypatch.setattr(scheduling, "_fetch_events", boom)
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    # A calendar read failure now RAISES ToolFault — the structured fault signal
    # the audit/health substrate keys off (PR-0) — carrying the same informative
    # message. The "never crashes the loop" guarantee moved to the run_tool /
    # execute seam, which catches it and records status="error"
    # (see test_audit_status.py).
    with pytest.raises(ToolFault, match="Error reading calendar"):
        scheduling._calendar_lookup({"range": "today"}, ctx)


def test_load_sa_info_json_and_base64():
    import base64, json
    from app.handlers import scheduling
    obj = {"type": "service_account", "client_email": "x@y.iam.gserviceaccount.com"}
    raw = json.dumps(obj)
    assert scheduling._load_sa_info(raw) == obj
    b64 = base64.b64encode(raw.encode()).decode()
    assert scheduling._load_sa_info(b64) == obj
    assert scheduling._load_sa_info("") is None
