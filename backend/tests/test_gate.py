"""Confirmation-gate logic — the safety-critical path."""
from app.config import settings
from app.handlers.base import Registry
from app.models import ActionAudit, PendingConfirmation
from app.orchestrator import _needs_confirmation, run
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
