from app.config import settings
from app.handlers.base import Context, build_registry


def test_order_tool_at_top_level_disabled_by_default(db):
    reg = build_registry(include_delegate=True)  # governed top-level registry
    assert reg.has("place_stock_order")
    assert not reg.is_gated("place_stock_order")  # disabled stub is not gated
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = reg.execute("place_stock_order", {"symbol": "AAPL", "qty": 1, "side": "buy"}, ctx)
    assert "DISABLED" in out


def test_trading_not_in_subagent_registry():
    reg = build_registry()  # sub-agent registry
    assert reg.has("get_stock_price") and reg.has("get_portfolio")  # read-only lives here
    assert not reg.has("place_stock_order")  # governed action never in a sub-agent
    assert not reg.has("delegate")           # no recursion


def test_order_tool_gated_when_trading_enabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    reg = build_registry(include_delegate=True)
    assert reg.is_gated("place_stock_order")
