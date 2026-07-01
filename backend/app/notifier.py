"""Outbound email via Gmail SMTP (app password). Supports in-thread replies.

Reused by the email ingest pipeline (to reply to commands) and, later, by the
scheduler/confirmation flows. Mirrors the FFIS send pattern.
"""

import smtplib
from email.mime.text import MIMEText
from email.utils import make_msgid

from app.config import settings


def send_email(
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Send a plain-text email. Returns the new message's Message-ID."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = settings.gmail_address
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg_id = make_msgid()
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.gmail_address, settings.gmail_app_password)
        server.send_message(msg)
    return msg_id
