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


def _safe(label: str, fn):
    """Run a data-source call; never let one failing source sink the briefing."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("briefing source '%s' failed: %s", label, e)
        return f"({label} unavailable right now: {e})"


def gather_context(db: Session) -> str:
    """Collect raw material from every live source into one text block."""
    from app.handlers.finance import _get_portfolio
    from app.handlers.scheduling import _calendar_lookup

    ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
    today = _safe("calendar", lambda: _calendar_lookup({"range": "today"}, ctx))
    week = _safe("calendar", lambda: _calendar_lookup({"range": "this week"}, ctx))
    portfolio = _safe("portfolio", lambda: _get_portfolio({}, ctx))

    facts = _safe("memory", lambda: db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(5)).scalars().all())
    if isinstance(facts, str):
        fact_lines = facts
    else:
        fact_lines = "\n".join(f"- {m.content}" for m in facts) or "(none)"

    sections = [f"## Today's calendar\n{today}", f"## This week\n{week}"]
    # Only include portfolio if a real brokerage is wired (skip demo/unavailable).
    if portfolio and not portfolio.startswith("[demo mode]") and not portfolio.startswith("(portfolio unavailable"):
        sections.append(f"## Portfolio\n{portfolio}")
    sections.append(f"## Recent notes/memory\n{fact_lines}")
    sections.append("## Not yet connected\n" + ", ".join(_PENDING_SECTIONS))
    return "\n\n".join(sections)


_BRIEF_INSTRUCTIONS = """
Write a concise morning briefing for your principal, in their voice and preferences.
Lead with today's schedule (most important), then a short look at the week, then a
one-line portfolio note. Keep it tight and scannable — short lines or compact bullets,
no filler. If a section says it's not connected or has no data, omit it or note it in
one short phrase; do not invent anything. End with a brief, useful nudge if warranted.
"""


def compose_briefing(db: Session) -> str:
    data = gather_context(db)
    try:
        system = build_system_preamble(db) + "\n" + _BRIEF_INSTRUCTIONS
        resp = create_message(system=system, messages=[{"role": "user", "content": data}])
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        text = "\n".join(parts).strip()
        if text:
            return text
    except Exception as e:  # noqa: BLE001
        log.error("briefing compose failed: %s", e)
        return f"Could not generate the written briefing ({e}).\n\nHere is the raw data:\n\n{data}"
    return "(no briefing generated)\n\n" + data


def send_briefing(db: Session) -> str:
    """Compose and email the briefing to the owner. Returns a status string."""
    from app.notifier import send_email

    to = settings.owner_email_resolved
    if not to:
        return "no owner email configured"
    text = compose_briefing(db)
    send_email(to, "Your JARVIS morning briefing", text)
    return f"briefing emailed to {to}"
