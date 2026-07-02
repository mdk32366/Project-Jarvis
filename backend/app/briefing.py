"""Morning briefing (Phase 2).

Assembles the sections we have live data for today — schedule (Google Calendar),
portfolio (Alpaca) — plus placeholders for sources not yet wired (bills, travel,
projects, app health/spend), and has the LLM compose a concise, warm briefing in
the principal's voice. Delivered on demand (/api/briefing) or on a daily schedule
(the worker enqueues a `morning_briefing` job → emails the owner).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.handlers.base import Context
from app.llm import create_message
from app.memory import build_system_preamble
from app.models import Memory

log = logging.getLogger(__name__)

# Sources not yet integrated — shown so the briefing is honest about coverage.
_PENDING_SECTIONS = ["Upcoming bills", "Weekend & travel", "Project status", "Hosted-app health & spend"]


def gather_context(db: Session) -> str:
    """Collect raw material from every live source into one text block."""
    from app.handlers.finance import _get_portfolio
    from app.handlers.scheduling import _calendar_lookup

    ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
    today = _calendar_lookup({"range": "today"}, ctx)
    week = _calendar_lookup({"range": "this week"}, ctx)
    portfolio = _get_portfolio({}, ctx)

    facts = db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(5)).scalars().all()
    fact_lines = "\n".join(f"- {m.content}" for m in facts) or "(none)"

    return (
        f"## Today's calendar\n{today}\n\n"
        f"## This week\n{week}\n\n"
        f"## Portfolio\n{portfolio}\n\n"
        f"## Recent notes/memory\n{fact_lines}\n\n"
        f"## Not yet connected\n" + ", ".join(_PENDING_SECTIONS)
    )


_BRIEF_INSTRUCTIONS = """
Write a concise morning briefing for your principal, in their voice and preferences.
Lead with today's schedule (most important), then a short look at the week, then a
one-line portfolio note. Keep it tight and scannable — short lines or compact bullets,
no filler. If a section says it's not connected or has no data, omit it or note it in
one short phrase; do not invent anything. End with a brief, useful nudge if warranted.
"""


def compose_briefing(db: Session) -> str:
    system = build_system_preamble(db) + "\n" + _BRIEF_INSTRUCTIONS
    data = gather_context(db)
    resp = create_message(system=system, messages=[{"role": "user", "content": data}])
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no briefing generated)"


def send_briefing(db: Session) -> str:
    """Compose and email the briefing to the owner. Returns a status string."""
    from app.notifier import send_email

    to = settings.owner_email_resolved
    if not to:
        return "no owner email configured"
    text = compose_briefing(db)
    send_email(to, "Your JARVIS morning briefing", text)
    return f"briefing emailed to {to}"
