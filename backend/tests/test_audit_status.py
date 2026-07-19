"""PR-0 — audit status must reflect the real outcome of a tool call.

Before this, `actions_audit` hardcoded `status="ok"` for every executed tool, so
a failed Calendar/Duffel/Tavily call was recorded as a success. Liveness (health
TDD §5.1) derives `last_success_at`/`last_failure_at` from that status; on an
all-"ok" substrate it can NEVER detect a failure — the status page would render
permanent green with no ability to go red.

These tests pin the substrate:
  * a tool that faults is recorded as `error`,
  * a healthy call as `ok`,
  * a handler's carefully-worded message survives verbatim (no wrapper),
  * `execute()` stays backward-compatible for every existing caller,
  * the gate decisions `confirmed`/`refused` are written literally, untouched.
"""

import pytest

from app.handlers.base import Context, Registry, ToolFault, build_registry


def _ctx(db):
    return Context(db=db, channel="web", actor="me", thread_key="t")


def _reg_with(fn):
    reg = Registry()
    reg.register(
        {"name": "probe", "description": "d",
         "input_schema": {"type": "object", "properties": {}}},
        fn,
    )
    return reg


# ── run_tool: outcome-derived status ─────────────────────────────────────────

def test_run_tool_ok(db):
    reg = _reg_with(lambda a, c: "all good")
    result, status = reg.run_tool("probe", {}, _ctx(db))
    assert result == "all good"
    assert status == "ok"


def test_run_tool_toolfault_preserves_message_verbatim(db):
    def boom(a, c):
        raise ToolFault("Duffel rejected the API key. Check DUFFEL_API_KEY.")

    reg = _reg_with(boom)
    result, status = reg.run_tool("probe", {}, _ctx(db))
    assert status == "error"
    # No "Error in probe:" wrapper — the user still sees the handler's guidance.
    assert result == "Duffel rejected the API key. Check DUFFEL_API_KEY."


def test_run_tool_unexpected_exception_is_error(db):
    def boom(a, c):
        raise RuntimeError("kaboom")

    reg = _reg_with(boom)
    result, status = reg.run_tool("probe", {}, _ctx(db))
    assert status == "error"
    assert "Error in probe" in result and "kaboom" in result


def test_run_tool_unknown_tool_is_error(db):
    reg = Registry()
    result, status = reg.run_tool("nope", {}, _ctx(db))
    assert status == "error"
    assert result == "Unknown tool: nope"


# ── execute(): unchanged public contract ─────────────────────────────────────

def test_execute_backward_compatible_ok(db):
    reg = _reg_with(lambda a, c: "ok result")
    assert reg.execute("probe", {}, _ctx(db)) == "ok result"


def test_execute_backward_compatible_fault_returns_string(db):
    def boom(a, c):
        raise ToolFault("nope happened")

    reg = _reg_with(boom)
    # execute() must never raise into the loop — it still returns the string.
    assert reg.execute("probe", {}, _ctx(db)) == "nope happened"


# ── the audit row itself ─────────────────────────────────────────────────────

def test_audit_persists_error_status(db):
    from app.models import ActionAudit
    from app.orchestrator import _audit

    _audit(db, _ctx(db), "probe", {}, "boom", "error")
    row = db.query(ActionAudit).one()
    assert row.status == "error"
    assert row.tool == "probe"


def test_audit_preserves_confirmed_and_refused(db):
    """Gate decisions are NOT outcome-derived: a confirmed/refused row is the
    record of the human-in-the-loop decision and must stay ok-family, never
    'error' (per the build order — a refused booking is a healthy system)."""
    from app.models import ActionAudit
    from app.orchestrator import _audit

    _audit(db, _ctx(db), "send_email", {}, "sent", "confirmed")
    _audit(db, _ctx(db), "book_flight", {}, "declined", "refused")
    statuses = {r.tool: r.status for r in db.query(ActionAudit).all()}
    assert statuses["send_email"] == "confirmed"
    assert statuses["book_flight"] == "refused"


# ── marquee end-to-end: a real external-API handler now faults as `error` ─────

def test_calendar_upstream_failure_records_error(db, monkeypatch):
    """The failure class that started all this: a Calendar auth/read failure.
    It must travel handler → registry → status='error', with the message intact,
    so liveness has a real failure to see."""
    import app.handlers.scheduling as sched

    monkeypatch.setattr(sched, "_service", lambda: object())  # pretend configured

    def _boom(*a, **k):
        raise RuntimeError("401 invalid_grant")

    monkeypatch.setattr(sched, "_fetch_events", _boom)

    reg = build_registry()  # sub-agent registry — where calendar_lookup lives
    assert reg.has("calendar_lookup")
    result, status = reg.run_tool("calendar_lookup", {"range": "today"}, _ctx(db))
    assert status == "error"
    assert "calendar" in result.lower()  # message preserved, user still informed


def test_calendar_success_records_ok(db, monkeypatch):
    import app.handlers.scheduling as sched

    monkeypatch.setattr(sched, "_service", lambda: object())
    monkeypatch.setattr(sched, "_fetch_events", lambda *a, **k: [])

    reg = build_registry()  # sub-agent registry — where calendar_lookup lives
    result, status = reg.run_tool("calendar_lookup", {"range": "today"}, _ctx(db))
    assert status == "ok"
