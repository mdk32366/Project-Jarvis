"""Finance handler — Alpaca stock quotes, portfolio, and (gated) orders.

Read-only tools (price, portfolio) are always available and fall back to a clear
"demo mode" message when no Alpaca keys are configured.

Trading (`place_stock_order`) is a REAL money action. It is hard-disabled by
default via `settings.enable_trading`. While disabled we still register the tool
so Claude can explain *why* it can't trade, but the handler refuses and nothing
is placed. When trading is re-enabled (after the dashboard has proper security),
the order flows through the orchestrator's human-in-the-loop confirmation gate.
"""

import json
from typing import Optional

from app.config import settings
from app.handlers.base import Context, Registry


def _api():
    """Return an Alpaca REST client, or None if not configured."""
    if not (settings.alpaca_api_key and settings.alpaca_secret_key):
        return None
    from alpaca_trade_api.rest import REST

    base = "https://paper-api.alpaca.markets" if settings.alpaca_paper else "https://api.alpaca.markets"
    return REST(settings.alpaca_api_key, settings.alpaca_secret_key, base_url=base)


def _get_stock_price(args: dict, ctx: Context) -> str:
    symbol = args["symbol"].upper()
    api = _api()
    if api is None:
        return f"[demo mode] No Alpaca keys configured; cannot fetch live price for {symbol}."
    trade = api.get_latest_trade(symbol)
    return f"{symbol} last trade: ${float(trade.price):.2f}"


def _get_portfolio(args: dict, ctx: Context) -> str:
    api = _api()
    if api is None:
        return "[demo mode] No Alpaca keys configured; cannot fetch portfolio."
    acct = api.get_account()
    positions = api.list_positions()
    lines = [f"Cash: ${float(acct.cash):.2f}  Equity: ${float(acct.equity):.2f}"]
    for p in positions:
        lines.append(f"  {p.symbol}: {p.qty} @ ${float(p.avg_entry_price):.2f} (mv ${float(p.market_value):.2f})")
    return "\n".join(lines) if positions else lines[0] + "\n  (no open positions)"


# Message returned to the model (and surfaced to the user) while trading is off.
TRADING_DISABLED_MSG = (
    "Trading is currently DISABLED on this JARVIS instance (ENABLE_TRADING=false). "
    "No order was placed. Tell the user trading is turned off pending stronger "
    "dashboard security, and do not attempt to place orders another way."
)


def _place_stock_order_disabled(args: dict, ctx: Context) -> str:
    return TRADING_DISABLED_MSG


def _place_stock_order(args: dict, ctx: Context) -> str:
    symbol = args["symbol"].upper()
    qty = float(args["qty"])
    side = args["side"]
    api = _api()
    if api is None:
        return f"[demo mode] Would {side} {qty} {symbol}, but no Alpaca keys are configured."
    order = api.submit_order(symbol=symbol, qty=qty, side=side, type="market", time_in_force="day")
    return json.dumps({"id": order.id, "symbol": symbol, "qty": qty, "side": side, "status": order.status})


def _order_notional(args: dict) -> Optional[float]:
    """Best-effort estimate of dollars at risk, for the confirmation threshold."""
    api = _api()
    qty = float(args.get("qty", 0))
    if api is None:
        return None  # unknown -> gate treats as needing confirmation to be safe
    try:
        price = float(api.get_latest_trade(args["symbol"].upper()).price)
        return qty * price
    except Exception:
        return None


def register(reg: Registry) -> None:
    """Read-only market-data tools. Used by the `finance` specialist agent."""
    reg.register(
        {
            "name": "get_stock_price",
            "description": "Get the latest trade price for a stock ticker.",
            "input_schema": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "Ticker, e.g. AAPL"}},
                "required": ["symbol"],
            },
        },
        _get_stock_price,
    )

    reg.register(
        {
            "name": "get_portfolio",
            "description": "Get current account balances and open positions.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _get_portfolio,
    )


def register_trading(reg: Registry) -> None:
    """The trade action. Registered ONLY on the governed top-level registry so it
    always flows through the confirmation gate (sub-agents bypass the gate).
    Disabled by default via settings.enable_trading."""
    if settings.enable_trading:
        order_desc = (
            "Place a market buy/sell order. This is a real financial action; "
            "the system will require the user's confirmation before it executes."
        )
        reg.register(
            {
                "name": "place_stock_order",
                "description": order_desc,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "qty": {"type": "number", "description": "Number of shares"},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                    },
                    "required": ["symbol", "qty", "side"],
                },
            },
            _place_stock_order,
            gated=True,
            notional=_order_notional,
            summarize=lambda i: f"{i.get('side','?')} {i.get('qty','?')} share(s) of {str(i.get('symbol','?')).upper()} at market",
        )
    else:
        reg.register(
            {
                "name": "place_stock_order",
                "description": (
                    "Place a stock order. NOTE: trading is currently DISABLED on this "
                    "instance; calling this will not place any order. Use it only to "
                    "inform the user that trading is turned off."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "qty": {"type": "number"},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                    },
                    "required": ["symbol", "qty", "side"],
                },
            },
            _place_stock_order_disabled,
        )
