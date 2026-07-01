"""Email ingest channel — the Phase 0 anchor.

Forked from the FFIS pipeline pattern: poll a dedicated Gmail inbox over IMAP,
take each unread message from a whitelisted sender, hand its text to the
orchestrator, and reply in-thread over SMTP. Runs headless as the Fly `ingest`
process.

  python -m app.channels.email_pipeline --once
  python -m app.channels.email_pipeline --watch --interval 120
"""

import argparse
import email as email_lib
import imaplib
import logging
import sys
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import ContactWhitelist
from app.notifier import send_email
from app.orchestrator import run as orchestrate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ingest] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg) -> str:
    """Extract the plain-text body, preferring text/plain parts."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(
                part.get("Content-Disposition", "")
            ):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""


def _is_allowed(db, sender_email: str) -> bool:
    sender = sender_email.lower()
    if sender in settings.allowed_sender_list:
        return True
    row = (
        db.execute(select(ContactWhitelist).where(ContactWhitelist.identifier == sender))
        .scalars()
        .first()
    )
    return row is not None


def process_inbox(send: bool = True) -> int:
    """Process unseen messages once. Returns number handled."""
    if not (settings.gmail_address and settings.gmail_app_password):
        log.warning("Gmail not configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD); skipping poll.")
        return 0

    imap = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
    imap.login(settings.gmail_address, settings.gmail_app_password)
    imap.select(settings.imap_folder)
    _, data = imap.search(None, "UNSEEN")
    ids = data[0].split()
    handled = 0
    db = SessionLocal()
    try:
        for mid in ids:
            _, msg_data = imap.fetch(mid, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])
            sender = parseaddr(msg.get("From", ""))[1].lower()
            subject = _decode(msg.get("Subject", "")) or "(no subject)"
            message_id = msg.get("Message-ID", "")

            if not _is_allowed(db, sender):
                log.info("Ignoring message from non-whitelisted sender: %s", sender)
                imap.store(mid, "+FLAGS", "\\Seen")
                continue

            body = _body_text(msg).strip()
            log.info("Handling message from %s: %s", sender, subject[:60])

            # Thread on the sender so a later "yes" reply resolves the pending action.
            reply = orchestrate(
                db=db, channel="email", thread_key=sender, user_text=body, actor=sender, subject=subject
            )

            if send:
                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                try:
                    send_email(sender, reply_subject, reply, in_reply_to=message_id)
                except Exception as e:
                    log.error("Failed to send reply to %s: %s", sender, e)

            imap.store(mid, "+FLAGS", "\\Seen")
            handled += 1
    finally:
        db.close()
        try:
            imap.logout()
        except Exception:
            pass
    log.info("Processed %d message(s).", handled)
    return handled


def watch(interval: int) -> None:
    log.info("Email ingest watching every %ss (account: %s)", interval, settings.gmail_address or "UNSET")
    while True:
        try:
            process_inbox(send=True)
        except Exception as e:
            log.error("Poll error: %s", e)
        time.sleep(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS email ingest")
    ap.add_argument("--once", action="store_true", help="process the inbox a single time")
    ap.add_argument("--watch", action="store_true", help="poll continuously")
    ap.add_argument("--interval", type=int, default=settings.ingest_poll_seconds)
    ap.add_argument("--no-send", action="store_true", help="do not send replies (debug)")
    args = ap.parse_args(argv)

    if args.watch:
        watch(args.interval)
    else:
        process_inbox(send=not args.no_send)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
