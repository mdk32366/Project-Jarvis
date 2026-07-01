"""SMS channel — inbound text -> whitelist -> orchestrator -> reply.

Unlike email (polled), SMS is push: Twilio POSTs an inbound webhook to
/api/sms/inbound (wired in routes.py). This module holds the channel logic so it
is unit-testable without HTTP: whitelist check + orchestration. The route returns
the reply as TwiML. (Fact reflection is enqueued centrally by the orchestrator.)
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import normalize_number, settings
from app.models import ContactWhitelist
from app.orchestrator import run as orchestrate

log = logging.getLogger(__name__)

CHANNEL = "sms"


def is_allowed(db: Session, number: str) -> bool:
    num = normalize_number(number)
    if num and num in settings.allowed_number_list:
        return True
    rows = (
        db.execute(select(ContactWhitelist).where(ContactWhitelist.channel == CHANNEL))
        .scalars()
        .all()
    )
    return any(normalize_number(r.identifier) == num for r in rows)


def handle_inbound(db: Session, from_number: str, body: str) -> str | None:
    """Process one inbound SMS. Returns reply text, or None if not whitelisted."""
    from_number = normalize_number(from_number)
    if not is_allowed(db, from_number):
        log.info("Ignoring SMS from non-whitelisted number: %s", from_number)
        return None
    return orchestrate(
        db=db, channel=CHANNEL, thread_key=from_number, user_text=(body or "").strip(), actor=from_number
    )


def to_twiml(message: str) -> str:
    """Wrap a reply as a TwiML <Response>. Empty message => empty response."""
    if not message:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{escape(message)}</Message></Response>"
    )
