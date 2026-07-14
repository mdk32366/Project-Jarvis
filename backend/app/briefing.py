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
_PENDING_SECTIONS = ["Upcoming bills", "Weekend & travel", "Project status"]


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
    from app.handlers.infra import _fleet_health, _fleet_spend
    from app.handlers.tasks import open_task_summary
    from app.handlers.travel import _list_trips

    ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
    today = _safe("calendar", lambda: _calendar_lookup({"range": "today"}, ctx))
    week = _safe("calendar", lambda: _calendar_lookup({"range": "this week"}, ctx))
    portfolio = _safe("portfolio", lambda: _get_portfolio({}, ctx))
    health = _safe("infra", lambda: _fleet_health({}, ctx))
    spend = _safe("infra", lambda: _fleet_spend({}, ctx))
    tasks = _safe("tasks", lambda: open_task_summary(db))
    trips = _safe("trips", lambda: _list_trips({}, ctx))

    facts = _safe("memory", lambda: db.execute(select(Memory).order_by(Memory.created_at.desc()).limit(5)).scalars().all())
    if isinstance(facts, str):
        fact_lines = facts
    else:
        fact_lines = "\n".join(f"- {m.content}" for m in facts) or "(none)"

    sections = [f"## Today's calendar\n{today}", f"## This week\n{week}"]
    # Open tasks: always worth surfacing — this is the list JARVIS owns.
    if isinstance(tasks, str) and tasks and not tasks.startswith("No open tasks"):
        sections.append(f"## Open tasks\n{tasks}")
    # Upcoming trips (captured from confirmation emails).
    if isinstance(trips, str) and trips and not trips.startswith("No trips on file"):
        sections.append(f"## Travel\n{trips}")
    # Only include portfolio if a real brokerage is wired (skip demo/unavailable).
    if portfolio and not portfolio.startswith("[demo mode]") and not portfolio.startswith("(portfolio unavailable"):
        sections.append(f"## Portfolio\n{portfolio}")
    sections.append(f"## Recent notes/memory\n{fact_lines}")
    # Hosted apps — only when a Fly token is configured (mirror portfolio skip).
    if isinstance(health, str) and not health.startswith("[infra not configured]"):
        block = health
        if isinstance(spend, str) and not spend.startswith("[infra not configured]"):
            block += "\n\n" + spend
        sections.append(f"## Hosted apps\n{block}")
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
        # §4.2 forced-first-call pattern: ground the LLM in real current time
        # before it reasons about "today", "this week", or anything date-relative.
        # Without this, the model infers "now" from its training data — which is
        # what produced the wrong-time briefing content (the scheduler's own clock
        # was correct; the LLM composing the spoken text was not).
        from app.handlers.datetime_tools import _get_current_datetime
        from app.handlers.base import Context
        _ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
        dt_ctx = _get_current_datetime({}, _ctx)
        grounded_data = f"[Current date/time: {dt_ctx}]\n\n{data}"

        system = build_system_preamble(db) + "\n" + _BRIEF_INSTRUCTIONS
        resp = create_message(system=system, messages=[{"role": "user", "content": grounded_data}])
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
