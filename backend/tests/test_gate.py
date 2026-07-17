"""Confirmation-gate logic — the safety-critical path."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import settings
from app.handlers.base import Registry
from app.models import ActionAudit, PendingConfirmation
from app.orchestrator import _AFFIRMATIVE, _NEGATIVE, _bare_match, _needs_confirmation, run
from fakes import install_llm, use_tool_then, say


def _gated_registry():
    reg = Registry()
    reg.register(
        {"name": "danger", "description": "x", "input_schema": {"type": "object", "properties": {}}},
        lambda args, ctx: "executed",
        gated=True,
        notional=lambda args: args.get("amount"),
        summarize=lambda i: "do the dangerous thing",
    )
    return reg


def test_threshold_logic():
    reg = _gated_registry()
    assert _needs_confirmation(reg, "danger", {"amount": None}) is True   # unknown -> gate
    assert _needs_confirmation(reg, "danger", {"amount": 10}) is False    # below $50
    assert _needs_confirmation(reg, "danger", {"amount": 1000}) is True   # above threshold


def test_create_event_confirms_only_when_attendees_will_be_emailed():
    """The gate on create_event exists to stop unreviewed email (invites), not
    calendar writes. No attendees -> no email leaves -> no confirmation."""
    from app.handlers import scheduling

    reg = Registry()
    scheduling.register_gated(reg)
    solo = {"title": "Dentist", "start": "2026-07-20T14:00"}
    assert _needs_confirmation(reg, "create_event", solo) is False
    assert _needs_confirmation(reg, "create_event", {**solo, "attendees": ""}) is False
    assert _needs_confirmation(reg, "create_event", {**solo, "attendees": "   "}) is False
    invited = {**solo, "attendees": "dave@example.com"}
    assert _needs_confirmation(reg, "create_event", invited) is True


def test_buy_creates_pending_not_executed(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "I'd like to buy 3 AAPL — reply yes to confirm.", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    pend = db.query(PendingConfirmation).all()
    assert len(pend) == 1 and pend[0].status == "pending"
    # nothing executed yet -> no 'confirmed' audit row
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_yes_confirms_and_executes(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "buy 3 AAPL — reply yes", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    reply = run(db, channel="sms", thread_key="+1555", user_text="yes", actor="+1555")
    assert "Done" in reply
    assert db.query(PendingConfirmation).first().status == "done"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 1


def test_no_cancels(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "buy 3 AAPL — reply yes", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    reply = run(db, channel="sms", thread_key="+1555", user_text="no", actor="+1555")
    assert "Cancelled" in reply
    assert db.query(PendingConfirmation).first().status == "cancelled"


def test_ambiguous_does_not_execute(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    # First msg creates pending. Ambiguous reply falls through to a plain answer.
    install_llm(monkeypatch, use_tool_then(
        "buy 3 AAPL — reply yes", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    install_llm(monkeypatch, say("The current price is around $200."))
    run(db, channel="sms", thread_key="+1555", user_text="what's the price first?", actor="+1555")
    assert db.query(PendingConfirmation).first().status == "pending"  # still pending
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


# ── Buffering hygiene: stale + instruction-carrying confirmations (audit) ─────
def _make_pending(db, monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "buy 3 AAPL — reply yes", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    return db.query(PendingConfirmation).first()


def test_bare_match_distinguishes_confirmations_from_new_instructions():
    assert _bare_match("yes", _AFFIRMATIVE)
    assert _bare_match("yes please", _AFFIRMATIVE)
    assert _bare_match("go ahead", _AFFIRMATIVE)
    assert _bare_match("do it", _AFFIRMATIVE)
    # THE bug: a 'yes' that carries a new instruction must NOT read as confirmation.
    assert not _bare_match("yes please run it now for part numbers and videos", _AFFIRMATIVE)
    assert not _bare_match("yes email dave the report", _AFFIRMATIVE)
    assert _bare_match("no", _NEGATIVE)
    assert _bare_match("cancel that", _NEGATIVE)
    assert not _bare_match("no instead research the tractor", _NEGATIVE)


def test_a_stale_pending_is_not_fired_by_a_later_yes(db, monkeypatch):
    """A 36-hour-old pending must not be executed by an unrelated later 'yes'."""
    pend = _make_pending(db, monkeypatch)
    assert pend.status == "pending"
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=settings.pending_confirmation_ttl_seconds + 3600)).replace(tzinfo=None)
    db.execute(text("UPDATE pending_confirmations SET created_at = :t WHERE id = :i"),
               {"t": old, "i": pend.id})
    db.commit()

    install_llm(monkeypatch, say("Sure — what do you need?"))
    run(db, channel="sms", thread_key="+1555", user_text="yes", actor="+1555")

    db.refresh(pend)
    assert pend.status == "expired", "stale pending should expire, not fire"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_yes_carrying_a_new_instruction_does_not_fire_a_fresh_pending(db, monkeypatch):
    """'Yes please check the weather' is a new request, not a confirmation of the
    buffered order — the pending must stay pending and nothing executes."""
    pend = _make_pending(db, monkeypatch)
    install_llm(monkeypatch, say("Looking into tomorrow's weather."))
    run(db, channel="sms", thread_key="+1555",
        user_text="yes please check the weather for tomorrow", actor="+1555")
    db.refresh(pend)
    assert pend.status == "pending"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_a_fresh_bare_yes_still_confirms(db, monkeypatch):
    """Don't over-correct: a normal, timely 'yes' must still execute the action."""
    _make_pending(db, monkeypatch)
    reply = run(db, channel="sms", thread_key="+1555", user_text="yes", actor="+1555")
    assert "Done" in reply
    assert db.query(PendingConfirmation).first().status == "done"
