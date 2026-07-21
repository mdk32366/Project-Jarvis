"""AutoRemote — the outbound nudge that asks the phone for a position fix.

Why this exists at all: Tasker on the owner's device cannot hold
SCHEDULE_EXACT_ALARM, so a phone-side timed profile is deferred indefinitely by
doze and simply never fires (correct config, no fires, empty run log — nothing
visible wrong). AutoRemote delivers over high-priority FCM, which Android *does*
deliver through doze. Moving the trigger off the phone removes the failure class
instead of working around it.

Placement note: the TDD calls this `backend/integrations/autoremote.py`, but this
codebase keeps outbound transports in `app/providers/` (see `sms.py`) and has no
`integrations` package. Same role, existing convention.

THE KEY IS A SECRET. It is never logged and never returned in an error string —
`dispatch_error` is stored in the database and surfaced on the status page, so a
key that leaked into it would leak twice. Asserted in test, not left to care.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://autoremotejoaomgcd.appspot.com/sendmessage"
_TIMEOUT = 10.0

# The message filter the phone's Tasker Event profile matches on. The payload is
# `jarvis_locreq=:=<nonce>`; AutoRemote splits on `=:=` and hands the phone the
# nonce as %arpar1.
MESSAGE_PREFIX = "jarvis_locreq"


def _scrub(text: str) -> str:
    """Remove the key from anything on its way to a log line or the database.

    Defence in depth: nothing here should ever contain the key, but httpx puts the
    request URL in some transport exceptions, and that URL carries the key as a
    query parameter. One cheap pass beats hoping.
    """
    key = settings.autoremote_key
    return text.replace(key, "***") if key else text


def send(message: str) -> tuple[bool, str | None]:
    """POST a message to the device. Returns `(dispatch_ok, error)`.

    The caller records BOTH — a dispatch that failed is the scheduler's fault and
    must be distinguishable from a phone that didn't answer, which is the entire
    point of the request record.

    Retries once on a connection error only, never on a non-200: a 4xx means the
    key or payload is wrong and will be exactly as wrong the second time, while a
    5xx retried in a tick loop is how you build an accidental hammer.
    """
    if not settings.autoremote_key:
        return False, "AUTOREMOTE_KEY is not configured"

    data = {"key": settings.autoremote_key, "message": message}
    last_err: str | None = None

    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.post(_ENDPOINT, data=data)
            if r.status_code == 200:
                log.info("autoremote dispatch ok: %s", message)
                return True, None
            # Body, not the request — the request echoes the key back.
            last_err = f"HTTP {r.status_code}: {_scrub(r.text)[:200]}"
            log.warning("autoremote dispatch failed: %s", last_err)
            return False, last_err
        except httpx.TransportError as e:
            last_err = _scrub(f"{type(e).__name__}: {e}")[:200]
            if attempt == 1:
                log.warning("autoremote transport error, retrying once: %s", last_err)
                continue
            log.error("autoremote dispatch failed after retry: %s", last_err)
        except Exception as e:  # noqa: BLE001 — dispatch must never raise into the tick
            last_err = _scrub(f"{type(e).__name__}: {e}")[:200]
            log.error("autoremote dispatch error: %s", last_err)
            break

    return False, last_err


def request_location(nonce: str) -> tuple[bool, str | None]:
    """Ask the phone for a fix, tagging the ask with `nonce` so the answer can be
    matched back to it."""
    return send(f"{MESSAGE_PREFIX}=:={nonce}")
