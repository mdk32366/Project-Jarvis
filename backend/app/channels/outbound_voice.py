"""Outbound voice — JARVIS calls the OWNER.

This is what makes her an assistant rather than an IVR.

The problem it solves: work that doesn't fit inside a phone call's poll budget
currently gets demoted to an email ("I'll email you the answer") or dies in a
log. A real assistant doesn't do that. She says "that'll take a few minutes,
I'll call you back" — and then she calls back.

What it unlocks:
  * The morning brief becomes a CALL, not an alarm you have to set.
  * Long research: hang up, work, ring back with the answer.
  * Watches: "tell me if that fare drops under $200."
  * A failed job can ASK for help instead of dying silently.

WHAT AN OUTBOUND CALL NEEDS THAT AN INBOUND ONE DOESN'T
-------------------------------------------------------
An opening line. On an inbound call the caller speaks first, so "JARVIS here"
suffices. Outbound, SHE rang — so she must open by saying who she is and WHY,
before the person answering can possibly know what this is about.

That opening is generated BEFORE dialling and stored on the row. Two reasons:
if we generated it on answer, there'd be dead air while an LLM thinks; and if
generation fails, we simply don't place the call, rather than ringing someone up
and then having nothing to say.

SAFETY
------
  * Only ever calls ALLOWED_NUMBERS. This is a hard check at dial time, not a
    convention — a bug that makes JARVIS cold-call a stranger is unacceptable.
  * Quiet hours. She does not ring at 3am.
  * Rate limited. A loop that dials in a tight cycle is a nightmare; the cap is
    a backstop against exactly that.
  * Answering-machine detection, so she doesn't monologue at voicemail.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import normalize_number, settings
from app.models import OutboundCall

log = logging.getLogger(__name__)

CHANNEL = "voice"


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def in_quiet_hours(now: datetime | None = None) -> bool:
    """Don't ring at 3am. Applies to alerts and other non-exempt calls.

    Two kinds are exempt because the owner asked for them at this time: a
    `callback` the user explicitly requested (if they said "call me back,"
    honouring that at 11pm is correct), and a scheduled `briefing` (the owner
    set the briefing time deliberately). Exemption for those is enforced in
    `due_calls`, not here.
    """
    now = (now or datetime.now(_tz())).astimezone(_tz()).time()
    start = time(settings.quiet_hours_start, settings.quiet_hours_start_minute)
    end = time(settings.quiet_hours_end, settings.quiet_hours_end_minute)
    if start < end:
        return start <= now < end
    return now >= start or now < end          # window wraps midnight


def _calls_placed_since(db: Session, since: datetime) -> int:
    return len(
        db.execute(
            select(OutboundCall)
            .where(OutboundCall.placed_at.is_not(None))
            .where(OutboundCall.placed_at >= since)
        )
        .scalars()
        .all()
    )


def schedule_call(
    db: Session,
    opening: str,
    kind: str = "callback",
    context: str = "",
    to_number: str = "",
    not_before: datetime | None = None,
) -> OutboundCall | None:
    """Queue a call. The worker places it. Returns None if the number isn't allowed.

    `opening` is what she SAYS when the call connects. Write it as speech.
    """
    to = normalize_number(to_number or settings.owner_phone)
    if not to:
        log.error("schedule_call: no number (set OWNER_PHONE)")
        return None

    # HARD CHECK. JARVIS may only ever ring the owner. This is not a convention
    # to be relied on elsewhere — a bug that cold-calls a stranger is
    # unacceptable, so it is enforced here AND again at dial time.
    if to not in settings.allowed_number_list:
        log.error("REFUSING to schedule a call to non-allowlisted number %s", to)
        return None

    row = OutboundCall(
        to_number=to,
        kind=kind,
        opening=(opening or "").strip()[:4000],
        context=(context or "")[:8000],
        not_before=not_before,
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("queued %s call #%s to %s", kind, row.id, to)
    return row


def due_calls(db: Session, now: datetime | None = None) -> list[OutboundCall]:
    """Queued calls that are allowed to go out right now."""
    now = now or datetime.now(_tz())
    rows = (
        db.execute(
            select(OutboundCall)
            .where(OutboundCall.status == "queued")
            .order_by(OutboundCall.id)
        )
        .scalars()
        .all()
    )

    out: list[OutboundCall] = []
    for r in rows:
        # SQLite hands back naive datetimes; Postgres hands back aware ones.
        # Comparing the two raises. Normalize rather than assume the backend.
        nb = r.not_before
        if nb is not None:
            if nb.tzinfo is None:
                nb = nb.replace(tzinfo=_tz())
            if nb > now:
                continue
        # A callback or a scheduled briefing the owner set the time for is exempt
        # from quiet hours — in both cases the owner asked for it at this time.
        if r.kind not in ("callback", "briefing") and in_quiet_hours(now):
            continue
        out.append(r)
    return out


def place_call(db: Session, row: OutboundCall) -> str:
    """Dial. Called by the worker, never inside a request."""
    from app.providers.sms import get_sms_provider

    # Second enforcement of the allowlist. Deliberately redundant: this is the
    # last line before a real phone actually rings.
    if normalize_number(row.to_number) not in settings.allowed_number_list:
        row.status = "failed"
        row.error = "number not in ALLOWED_NUMBERS"
        db.commit()
        log.error("REFUSED to dial non-allowlisted %s", row.to_number)
        return "refused"

    # Rate limit: a bug that dials in a loop is a genuine nightmare, and the
    # person on the other end can't easily make it stop.
    since = datetime.now(_tz()) - timedelta(hours=1)
    if _calls_placed_since(db, since) >= settings.max_outbound_calls_per_hour:
        log.warning("outbound rate limit hit — deferring call #%s", row.id)
        return "rate_limited"

    if not row.opening:
        row.status = "failed"
        row.error = "no opening line"
        db.commit()
        return "no opening"

    base = settings.voice_public_url_base.rstrip("/")
    url = f"{base}/api/voice/outbound?call={row.id}"

    try:
        sid = get_sms_provider().call(row.to_number, url)
    except Exception as e:  # noqa: BLE001
        row.status = "failed"
        row.error = str(e)[:2000]
        db.commit()
        log.error("call #%s failed: %s", row.id, e)
        return f"failed: {e}"

    row.call_sid = sid
    row.status = "ringing"
    row.placed_at = datetime.now(_tz())
    db.commit()
    log.info("placed %s call #%s -> %s (%s)", row.kind, row.id, row.to_number, sid)
    return f"calling {row.to_number}"


def get_by_id(db: Session, call_id: int) -> OutboundCall | None:
    return db.get(OutboundCall, call_id)


def get_by_sid(db: Session, sid: str) -> OutboundCall | None:
    return (
        db.execute(select(OutboundCall).where(OutboundCall.call_sid == sid))
        .scalars()
        .first()
    )
