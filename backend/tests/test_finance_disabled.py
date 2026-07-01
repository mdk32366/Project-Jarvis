from app.config import settings
from app.handlers.base import build_registry
from app.handlers.finance import TRADING_DISABLED_MSG
from app.handlers.base import Context


def test_order_tool_is_registered_but_disabled_by_default(db):
    reg = build_registry()
    assert reg.has("place_stock_order")
    assert not reg.is_gated("place_stock_order")  # disabled stub is not gated
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = reg.execute("place_stock_order", {"symbol": "AAPL", "qty": 1, "side": "buy"}, ctx)
    assert out == TRADING_DISABLED_MSG
    assert "DISABLED" in out


def test_read_only_finance_tools_present():
    reg = build_registry()
    assert reg.has("get_stock_price")
    assert reg.has("get_portfolio")


def test_order_tool_is_gated_when_trading_enabled(monkeypatch):
    monkeypatch.setattr(settings, "enable_trading", True)
    reg = build_registry()
    assert reg.is_gated("place_stock_order")
