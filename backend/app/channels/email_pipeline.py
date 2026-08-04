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
import re
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


# ── Quoted-reply stripping ───────────────────────────────────────────────────
# Mail is the one channel that quotes. A Gmail reply saying "Confirm." carries the
# entire prior thread underneath it, so the text reaching orchestrator.run() is the
# owner's word followed by hundreds of content words. `orchestrator._bare_match`
# requires EVERY token to be affirmative-or-filler, so it returns False
# unconditionally, the turn falls through to normal handling, the model reads the
# quoted request as a fresh one, and a NEW confirmation is raised. That is the
# 2026-08-04 latch: eleven confirmations, none resolved.
#
# The fix belongs here and not in the orchestrator. Quoting is a mail-transport
# artefact; putting the strip in the orchestrator would apply it to SMS, voice and
# chat bodies that never quote, and would make a mail concern load-bearing for
# every channel. `_bare_match` itself is correct and is deliberately left alone —
# the input was wrong, not the test.
#
# Pure: text in, text out. No DB, no network, no logging — the same shape as
# `app/secretscan.py`, and for the same reason: it is fully testable offline and
# the enforcement happens at the call site.

# Attribution line introducing a quote: "On Tue, Aug 4, 2026 at 6:19 AM X wrote:"
# (Gmail) and "On Aug 4, 2026, at 6:19 AM, X wrote:" (Apple Mail). Gmail hard-wraps
# long ones, so "wrote:" may land on the following line — see `_attribution_at`.
_RE_ATTRIBUTION = re.compile(r"^\s*On\b.*\bwrote:\s*$", re.IGNORECASE)
_RE_ATTRIBUTION_HEAD = re.compile(r"^\s*On\b.*[^\s]", re.IGNORECASE)
# Outlook / Exchange separators.
_RE_ORIGINAL_MESSAGE = re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE)
_RE_OUTLOOK_RULE = re.compile(r"^\s*_{5,}\s*$")
# RFC 3676 signature delimiter: a line that is exactly "-- " (trailing space is
# part of the standard, but plenty of clients trim it, so allow the bare form).
_RE_SIGNATURE = re.compile(r"^--\s*$")
# Outlook inline-reply header block. Matched only as a BLOCK — two consecutive
# header lines — because a lone "To: pick up milk" is prose, not a quote.
_RE_HEADER = re.compile(r"^\s*(From|Sent|To|Subject|Cc|Date|Reply-To):\s+\S", re.IGNORECASE)


def _attribution_at(lines: list[str], i: int) -> int:
    """Lines consumed by a quote attribution starting at `lines[i]`; 0 if none.

    Returns a LENGTH, not a bool, because Gmail hard-wraps a long attribution over
    two or three lines. The continuation lines carry no quote marker of their own,
    so the interleave check in `strip_quoted_text` would otherwise read "wrote:" as
    the owner typing below the quote.
    """
    if _RE_ATTRIBUTION.match(lines[i]):
        return 1
    # Gmail wraps: "On Tue, Aug 4, 2026 at 6:19 AM someone@example.com" / "wrote:".
    if _RE_ATTRIBUTION_HEAD.match(lines[i]):
        joined = lines[i]
        for offset, nxt in enumerate(lines[i + 1:i + 3], start=2):
            if not nxt.strip():
                break
            joined = f"{joined} {nxt.strip()}"
            if _RE_ATTRIBUTION.match(joined.strip()):
                return offset
    return 0


def _marker_at(lines: list[str], i: int) -> tuple[str, int] | None:
    """The quote marker starting at `lines[i]` as (kind, lines_consumed), or None.

    Two kinds, because they need different handling below the cut (see
    `strip_quoted_text`):
      "quoted" — an attribution line or a `>` level; what follows is quoted text.
      "block"  — a separator, header block, or signature; what follows is the
                 prior message rendered as ordinary unquoted prose.

    The span matters: a wrapped attribution occupies more than one line, and the
    interleave check must resume BELOW it rather than inside it.
    """
    line = lines[i]
    if not line.strip():
        return None
    if line.lstrip().startswith(">"):
        return "quoted", 1
    consumed = _attribution_at(lines, i)
    if consumed:
        return "quoted", consumed
    if _RE_ORIGINAL_MESSAGE.match(line) or _RE_OUTLOOK_RULE.match(line):
        return "block", 1
    if _RE_SIGNATURE.match(line):
        return "block", 1
    if _RE_HEADER.match(line):
        # Require a second header line to confirm it is a block, not prose.
        for nxt in lines[i + 1:i + 4]:
            if _RE_HEADER.match(nxt):
                return "block", 1
            if nxt.strip():
                break
    return None


def _is_interleaved_below(lines: list[str], cut: int) -> bool:
    """True if the owner has typed his own words BELOW a `>`-style quote level.

    Everything under the cut must be blank, `>`-prefixed, or a (possibly wrapped)
    nested attribution. Anything else is the owner writing inside the quote, which
    makes the message a new instruction rather than a bare confirmation.
    """
    i = cut + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith(">"):
            i += 1
            continue
        consumed = _attribution_at(lines, i)
        if consumed:
            i += consumed
            continue
        return True
    return False


def strip_quoted_text(body: str) -> str:
    """Return the owner's own words, with any quoted thread below them removed.

    Truncates at the FIRST quote marker and keeps everything above it. No attempt
    is made to reassemble an interleaved reply — where the owner has typed between
    quote levels the message is a NEW instruction, not a bare confirmation, and the
    correct outcome is to leave it intact so it falls through to normal handling.
    That is what the unstripped return below buys.

    The empty case is load-bearing: if stripping would yield nothing — a top-posted
    reply with nothing typed above the quote — the ORIGINAL body is returned. An
    empty `user_text` would be a different bug, and a stripper that can silently
    erase a message is worse than one that occasionally under-strips.
    """
    if not body or not body.strip():
        return body

    lines = body.splitlines()
    cut = kind = span = None
    for i in range(len(lines)):
        found = _marker_at(lines, i)
        if found is not None:
            kind, span = found
            cut = i
            break

    if cut is None:
        return body

    if kind == "quoted":
        # An interleaved reply — the owner typing BELOW a quote level — is a new
        # instruction. Detectable only for `>`-style quoting, where genuine quoted
        # lines carry the marker; a "block" quote is unmarked prose all the way
        # down and cannot be told apart this way.
        if _is_interleaved_below(lines, cut + span - 1):
            return body

    kept = "\n".join(lines[:cut]).strip()
    return kept if kept else body


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
                # Airlines are NOT whitelisted senders and never will be — the
                # whitelist governs who may COMMAND JARVIS. But a confirmation
                # email is not a command; it is data addressed to us. Capture the
                # itinerary (read-only, no orchestration, no reply) and then drop
                # the message as usual.
                #
                # This is the whole trust boundary for travel: JARVIS knows about
                # the trip because the airline mailed it here. No credentials, no
                # scraping, no account access.
                try:
                    from app.handlers.travel import looks_like_confirmation, record_trip_from_email

                    if looks_like_confirmation(subject, sender):
                        trip = record_trip_from_email(db, subject, _body_text(msg))
                        if trip is not None:
                            log.info("captured trip from %s: %s %s",
                                     sender, trip.carrier, trip.confirmation)
                except Exception as e:  # noqa: BLE001 — never let this break ingest
                    log.warning("trip capture failed for %s: %s", sender, e)

                log.info("Ignoring message from non-whitelisted sender: %s", sender)
                imap.store(mid, "+FLAGS", "\\Seen")
                continue

            # Strip the quoted thread HERE, at the single orchestrate() call site,
            # rather than inside `_body_text` — the trip-capture path above parses
            # an airline confirmation and needs the whole message, quotes and all.
            body = strip_quoted_text(_body_text(msg)).strip()
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
