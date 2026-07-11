"""Callback — JARVIS asks to ring the owner back.

The gap this closes. Watch what she used to do when work outran the poll budget:

    "That's taking longer than I can hold the line for. I'll email you."

A real assistant doesn't demote your request to an email. She says "give me a few
minutes and I'll call you back" — and then she calls back. That's the difference
between an IVR and an assistant, and it's this tool.

Uses:
  * Long research. Hang up, work, ring back with the answer.
  * A watch: "let me know if that fare drops under $200."
  * A stuck job that needs a decision, rather than dying in a log.
  * A reminder that's worth a ring rather than a notification.

NOT gated. Scheduling a call to the OWNER'S OWN NUMBER is not irreversible and
not dangerous — the number is hard-checked against ALLOWED_NUMBERS at both
schedule and dial time, so the worst case is a phone call the user didn't want,
which they can decline. Requiring "confirm" for every callback would make the
feature tiresome enough not to use.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import OutboundCall

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _parse_delay(raw: str) -> datetime | None:
    """'in 10 minutes' / 'in an hour' / '2pm'. None => as soon as possible."""
    if not raw:
        return None
    now = datetime.now(_tz())
    r = raw.strip().lower()

    if "hour" in r:
        n = 1
        for tok in r.split():
            if tok.isdigit():
                n = int(tok)
                break
        return now + timedelta(hours=n)
    if "min" in r:
        n = 10
        for tok in r.split():
            if tok.isdigit():
                n = int(tok)
                break
        return now + timedelta(minutes=n)

    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=_tz())
    except ValueError:
        return None       # unparseable => call as soon as possible, don't guess


def _call_me_back(args: dict, ctx: Context) -> str:
    from app.channels.outbound_voice import in_quiet_hours, schedule_call

    opening = (args.get("opening") or "").strip()
    reason = (args.get("reason") or "").strip()
    if not opening:
        return "Need an opening line — what will you SAY when they pick up?"

    when = _parse_delay(args.get("when") or "")
    row = schedule_call(
        ctx.db,
        opening=opening,
        kind="callback",
        context=reason,
        not_before=when,
    )
    if row is None:
        return ("I can't call you — OWNER_PHONE isn't set, or it isn't in "
                "ALLOWED_NUMBERS. I'll have to email you instead.")

    if when:
        return f"I'll call you back around {when.astimezone(_tz()).strftime('%-I:%M %p')}."
    if in_quiet_hours():
        # Callbacks are exempt from quiet hours — the user asked. But say so, so
        # a 2am ring isn't a surprise.
        return "I'll call you right back — note it's late, so this'll ring shortly."
    return "I'll call you right back."


def _pending_callbacks(args: dict, ctx: Context) -> str:
    rows = (
        ctx.db.execute(
            select(OutboundCall)
            .where(OutboundCall.status.in_(("queued", "ringing")))
            .order_by(OutboundCall.id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return "No calls pending."
    lines = []
    for r in rows:
        when = (r.not_before.astimezone(_tz()).strftime("%-I:%M %p")
                if r.not_before else "as soon as possible")
        lines.append(f"#{r.id} ({r.kind}, {when}): {r.opening[:70]}")
    return f"{len(rows)} pending:\n" + "\n".join(lines)


def _cancel_callback(args: dict, ctx: Context) -> str:
    cid = args.get("call_id")
    if cid is None:
        return "Which one? Give the call number."
    row = ctx.db.get(OutboundCall, int(cid))
    if row is None:
        return f"No call #{cid}."
    if row.status != "queued":
        return f"Call #{cid} is already {row.status} — too late to cancel."
    row.status = "failed"
    row.error = "cancelled by user"
    ctx.db.commit()
    return f"Cancelled call #{cid}."


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "call_me_back",
            "description": (
                "Schedule a phone call TO the user. Use this instead of falling back to "
                "email when work will take longer than you can hold the line for, when "
                "they ask you to follow up, or when something happens that's worth a "
                "ring. You hang up, do the work, and call them back."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "opening": {
                        "type": "string",
                        "description": (
                            "EXACTLY what you'll SAY when they pick up. They won't know why "
                            "you're calling, so lead with it: 'It's JARVIS — I've got those "
                            "flight results you asked for.' Written to be spoken: no "
                            "markdown, no lists."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Context for yourself, so you know what the call is "
                                       "about once they start talking.",
                    },
                    "when": {
                        "type": "string",
                        "description": "'in 10 minutes', 'in an hour', or an ISO time. "
                                       "Omit to call as soon as possible.",
                    },
                },
                "required": ["opening"],
            },
        },
        _call_me_back,
    )
    reg.register(
        {
            "name": "pending_callbacks",
            "description": "List calls JARVIS is planning to make.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _pending_callbacks,
    )
    reg.register(
        {
            "name": "cancel_callback",
            "description": "Cancel a pending call.",
            "input_schema": {
                "type": "object",
                "properties": {"call_id": {"type": "integer"}},
                "required": ["call_id"],
            },
        },
        _cancel_callback,
    )
