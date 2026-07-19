"""Outbound calling — JARVIS rings the owner.

The safety tests are the point. "She only ever calls the owner" is worthless as a
claim until it's demonstrated: a bug that cold-calls a stranger, or that dials in
a loop, is not something the person on the other end can easily make stop.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.channels import outbound_voice as ov
from app.config import settings
from app.handlers.base import Context
from app.models import OutboundCall


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="voice", actor="+15551230000", thread_key="t")


@pytest.fixture
def owner(monkeypatch):
    # conftest sets ALLOWED_NUMBERS=+15551230000
    monkeypatch.setattr(settings, "owner_phone", "+15551230000")
    monkeypatch.setattr(settings, "outbound_calls_enabled", True)
    monkeypatch.setattr(settings, "voice_public_url_base", "https://jarvis-mdk.fly.dev")


def _quiet(monkeypatch, start_h, start_m, end_h, end_m):
    """Set the quiet-hours window (hour + minute precision) for a test."""
    monkeypatch.setattr(settings, "quiet_hours_start", start_h)
    monkeypatch.setattr(settings, "quiet_hours_start_minute", start_m)
    monkeypatch.setattr(settings, "quiet_hours_end", end_h)
    monkeypatch.setattr(settings, "quiet_hours_end_minute", end_m)


# ── SAFETY: she may only ever ring the owner ─────────────────────────────────
def test_refuses_to_schedule_a_call_to_a_stranger(db, monkeypatch):
    """A bug that cold-calls someone is unacceptable. Enforced at SCHEDULE time."""
    monkeypatch.setattr(settings, "owner_phone", "+19998887777")   # not allowlisted

    row = ov.schedule_call(db, opening="hello", to_number="+19998887777")

    assert row is None
    assert db.query(OutboundCall).count() == 0


def test_refuses_to_DIAL_a_stranger_even_if_a_row_exists(db, owner, stub_sms):
    """Enforced AGAIN at dial time. Deliberately redundant — this is the last
    line before a real phone actually rings. If a row is corrupted, hand-edited,
    or written by a future bug, the dial must still refuse."""
    rogue = OutboundCall(to_number="+19998887777", opening="hi", status="queued")
    db.add(rogue)
    db.commit()

    result = ov.place_call(db, rogue)

    assert result == "refused"
    assert stub_sms.calls == [], "JARVIS DIALLED A NON-ALLOWLISTED NUMBER"
    assert rogue.status == "failed"


def test_rate_limited_so_a_loop_cannot_ring_someone_forever(db, owner, stub_sms, monkeypatch):
    monkeypatch.setattr(settings, "max_outbound_calls_per_hour", 2)
    tz = ZoneInfo(settings.calendar_timezone)

    for _ in range(2):
        r = ov.schedule_call(db, opening="x")
        ov.place_call(db, r)
    assert len(stub_sms.calls) == 2

    third = ov.schedule_call(db, opening="x")
    assert ov.place_call(db, third) == "rate_limited"
    assert len(stub_sms.calls) == 2, "rate limit didn't hold"


# ── Quiet hours ──────────────────────────────────────────────────────────────
def test_does_not_ring_at_3am(db, owner, monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours_start", 21)
    monkeypatch.setattr(settings, "quiet_hours_end", 7)
    tz = ZoneInfo(settings.calendar_timezone)
    three_am = datetime.now(tz).replace(hour=3, minute=0)

    assert ov.in_quiet_hours(db, three_am) is True

    # An ALERT is not owner-scheduled, so quiet hours hold it until morning.
    # Briefings and callbacks are exempt (the owner set/asked for those) — see
    # the exemption tests below.
    ov.schedule_call(db, opening="heads up", kind="alert")
    assert ov.due_calls(db, now=three_am) == []      # held until morning


def test_a_callback_the_user_ASKED_for_is_exempt_from_quiet_hours(db, owner, monkeypatch):
    """If they said 'call me back', honouring that at 11pm is correct.
    Second-guessing an explicit request is not."""
    monkeypatch.setattr(settings, "quiet_hours_start", 21)
    monkeypatch.setattr(settings, "quiet_hours_end", 7)
    tz = ZoneInfo(settings.calendar_timezone)
    eleven_pm = datetime.now(tz).replace(hour=23, minute=0)

    ov.schedule_call(db, opening="got your answer", kind="callback")

    assert len(ov.due_calls(db, now=eleven_pm)) == 1


# ── Quiet hours: minute-granularity window (e.g. 21:00–03:30) ────────────────
def test_quiet_minute_precision_inside_before_end(db, monkeypatch):
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=3, minute=15)) is True


def test_quiet_minute_precision_just_after_end(db, monkeypatch):
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=3, minute=45)) is False


def test_quiet_wraps_midnight_evening_is_inside(db, monkeypatch):
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=22, minute=0)) is True


def test_quiet_midday_is_outside(db, monkeypatch):
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=12, minute=0)) is False


def test_quiet_non_wrapping_window(db, monkeypatch):
    """A same-day window (start < end) still works with minute precision."""
    _quiet(monkeypatch, 13, 0, 14, 0)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=13, minute=30)) is True


def test_quiet_defaults_preserve_legacy_behavior(db, monkeypatch):
    """Minute fields default to 0, so a 21:00–07:00 window behaves exactly as it
    did before this change."""
    monkeypatch.setattr(settings, "quiet_hours_start", 21)
    monkeypatch.setattr(settings, "quiet_hours_end", 7)
    tz = ZoneInfo(settings.calendar_timezone)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=3, minute=0)) is True
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=12, minute=0)) is False


# ── Quiet hours: the briefing is exempt (owner set its time) ─────────────────
def test_due_calls_briefing_after_quiet_end_is_returned(db, owner, monkeypatch):
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    four_am = datetime.now(tz).replace(hour=4, minute=0)

    ov.schedule_call(db, opening="your brief", kind="briefing")
    assert len(ov.due_calls(db, now=four_am)) == 1


def test_due_calls_briefing_inside_quiet_is_exempt(db, owner, monkeypatch):
    """The exemption's whole point: a briefing fires even INSIDE quiet hours,
    because the owner deliberately set the briefing time."""
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    two_am = datetime.now(tz).replace(hour=2, minute=0)

    assert ov.in_quiet_hours(db, two_am) is True
    ov.schedule_call(db, opening="your brief", kind="briefing")
    assert len(ov.due_calls(db, now=two_am)) == 1


def test_due_calls_callback_inside_quiet_is_returned(db, owner, monkeypatch):
    """Unchanged: a callback the user asked for is exempt from quiet hours."""
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    two_am = datetime.now(tz).replace(hour=2, minute=0)

    ov.schedule_call(db, opening="got your answer", kind="callback")
    assert len(ov.due_calls(db, now=two_am)) == 1


def test_due_calls_alert_inside_quiet_is_suppressed(db, owner, monkeypatch):
    """Unchanged: an alert is NOT owner-scheduled, so quiet hours still hold it."""
    _quiet(monkeypatch, 21, 0, 3, 30)
    tz = ZoneInfo(settings.calendar_timezone)
    two_am = datetime.now(tz).replace(hour=2, minute=0)

    ov.schedule_call(db, opening="heads up", kind="alert")
    assert ov.due_calls(db, now=two_am) == []


def test_scheduled_for_later_is_not_placed_early(db, owner):
    tz = ZoneInfo(settings.calendar_timezone)
    later = datetime.now(tz) + timedelta(hours=2)

    ov.schedule_call(db, opening="x", kind="callback", not_before=later)

    assert ov.due_calls(db) == []
    assert len(ov.due_calls(db, now=later + timedelta(minutes=1))) == 1


# ── Placing the call ─────────────────────────────────────────────────────────
def test_place_call_dials_and_records_the_sid(db, owner, stub_sms):
    row = ov.schedule_call(db, opening="It's JARVIS with your flight results.")
    ov.place_call(db, row)

    assert len(stub_sms.calls) == 1
    to, url = stub_sms.calls[0]
    assert to == "+15551230000"
    assert f"/api/voice/outbound?call={row.id}" in url   # Twilio fetches TwiML here
    assert row.status == "ringing"
    assert row.call_sid.startswith("stub-call")


def test_will_not_call_with_nothing_to_say(db, owner, stub_sms):
    """The opening is generated BEFORE dialling. If it's missing, don't ring
    someone and then have nothing to say to them."""
    row = OutboundCall(to_number="+15551230000", opening="", status="queued")
    db.add(row)
    db.commit()

    ov.place_call(db, row)

    assert stub_sms.calls == []
    assert row.status == "failed"


# ── The tool ─────────────────────────────────────────────────────────────────
def test_call_me_back_queues_a_call(ctx, db, owner):
    from app.handlers.callback import _call_me_back

    out = _call_me_back({
        "opening": "It's JARVIS. I've got those flight results you asked for.",
        "reason": "SEA to SFO search the user asked for on the last call",
    }, ctx)

    assert "call you" in out.lower()
    row = db.query(OutboundCall).one()
    assert row.kind == "callback"
    assert "flight results" in row.opening
    assert "SEA to SFO" in row.context      # so she knows why she rang


def test_call_me_back_demands_an_opening_line(ctx, owner):
    """They have no idea why you're calling. Lead with it."""
    from app.handlers.callback import _call_me_back

    out = _call_me_back({"reason": "something"}, ctx)
    assert "opening" in out.lower()


def test_call_me_back_says_so_when_it_cannot_call(ctx, monkeypatch):
    from app.handlers.callback import _call_me_back

    monkeypatch.setattr(settings, "owner_phone", "")
    out = _call_me_back({"opening": "hi"}, ctx)
    assert "can't call you" in out.lower()
    assert "email" in out.lower()          # falls back honestly


def test_pending_and_cancel(ctx, db, owner):
    from app.handlers.callback import _call_me_back, _cancel_callback, _pending_callbacks

    _call_me_back({"opening": "ring ring"}, ctx)
    assert "ring ring" in _pending_callbacks({}, ctx)

    cid = db.query(OutboundCall).one().id
    assert "cancelled" in _cancel_callback({"call_id": cid}, ctx).lower()
    assert "No calls pending" in _pending_callbacks({}, ctx)


# ── The orchestrator is told to use it ───────────────────────────────────────
def test_voice_is_told_to_call_back_rather_than_email(ctx):
    """'I'll email you the answer' is what an IVR does."""
    from app.orchestrator import _VOICE_INSTRUCTIONS

    assert "call_me_back" in _VOICE_INSTRUCTIONS
    assert "do NOT demote it to" in _VOICE_INSTRUCTIONS


def test_call_me_back_is_reachable_from_voice():
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    assert "call_me_back" in VOICE_TOOLS_PHASE1
