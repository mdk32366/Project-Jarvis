"""Secretary handler — draft and send email.

`draft_email` is free: it composes and shows you the text, sends nothing.

`send_email` is **GATED**. It is irreversible and it speaks as you — an email
sent in your name to the wrong person cannot be recalled. It routes through the
orchestrator's confirmation gate (registered `gated=True` with no `notional`, so
`_needs_confirmation` returns True unconditionally), which means:

    JARVIS: "Readback: email to dave@example.com, subject 'Q3 numbers'.
             Confirm or cancel."
    You:    "Confirm."

On voice, the confirmation vocabulary is deliberately narrow — "ok" and "yeah"
will NOT trigger it (see orchestrator._VOCAB). An explicit "confirm" /
"affirmative" / "execute" is required. That is the point: sending mail as you is
a deliberate act, not an accident of conversational rhythm.
"""

from __future__ import annotations

import logging
import re

from app.config import settings
from app.handlers.base import Context, Registry

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _draft_email(args: dict, ctx: Context) -> str:
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    if not body:
        return "No body to draft."
    return (
        "Draft (NOT sent):\n"
        f"To: {to or '(unspecified)'}\n"
        f"Subject: {subject or '(none)'}\n\n"
        f"{body}\n\n"
        "Say send it if you want this sent."
    )


def _send_email(args: dict, ctx: Context) -> str:
    """Executed only AFTER the confirmation gate clears."""
    from app.notifier import send_email as smtp_send

    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip() or "(no subject)"
    body = (args.get("body") or "").strip()

    if not _EMAIL_RE.match(to):
        return f"'{to}' is not a valid email address. Nothing sent."
    if not body:
        return "Refusing to send an empty email."

    try:
        msg_id = smtp_send(to, subject, body)
    except Exception as e:  # noqa: BLE001
        log.error("send_email failed: %s", e)
        return f"Send failed: {e}"
    return f"Sent to {to} — subject: {subject}"


def _summarize_send(args: dict) -> str:
    """The readback line. Must state exactly what will happen, unambiguously."""
    to = args.get("to", "?")
    subject = args.get("subject") or "(no subject)"
    body = (args.get("body") or "").strip()
    preview = body[:60].replace("\n", " ")
    if len(body) > 60:
        preview += "..."
    return f"email {to}, subject '{subject}', beginning '{preview}'"


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "draft_email",
            "description": "Compose an email and show it to the user WITHOUT sending. "
                           "Use this first — always let them see it before sending.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["body"],
            },
        },
        _draft_email,
    )


def register_gated(reg: Registry) -> None:
    """Gated tools — registered ONLY on the top-level registry, where the
    confirmation gate actually runs. Sub-agents bypass the gate entirely, so a
    gated tool in a sub-agent roster would send with no confirmation at all."""
    reg.register(
        {
            "name": "send_email",
            "description": (
                "Send an email as the user. This is IRREVERSIBLE and the system will "
                "require the user's explicit confirmation before it executes. Prefer "
                "draft_email first so they can see it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient address."},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "body"],
            },
        },
        _send_email,
        gated=True,               # -> notional is None -> confirmation ALWAYS required
        summarize=_summarize_send,
    )
