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

**THE STATUS CODE IS NOT THE OUTCOME.** This relay answers `200 OK` to everything
and reports the real result in the BODY: `OK` when it accepted the message for a
registered device, `NotRegistered` when there is no device to deliver to, and
other short strings on error. The first version of this module checked only
`status_code == 200`, so it recorded success on every send while the relay was
answering `NotRegistered` — a total delivery failure that read green for the
entire life of the feature. Read the body. (Found 2026-07-21; the stored key
carried a literal `key=` prefix, which is what the relay was rejecting.)

THE KEY IS A SECRET. It is never logged and never returned in an error string —
`relay_error` is stored in the database and surfaced on the status page, so a key
that leaked into it would leak twice. Note `_scrub` handles the PERCENT-ENCODED
form as well: the key travels in a form-encoded body, so scrubbing only the raw
literal misses it entirely. That exact oversight leaked the key once already.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, quote_plus

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://autoremotejoaomgcd.appspot.com/sendmessage"
_TIMEOUT = 10.0

# The message filter the phone's Tasker Event profile matches on. The payload is
# `jarvis_locreq=:=<nonce>`; AutoRemote splits on `=:=` and hands the phone the
# nonce as %arpar1. The separator percent-encodes to %3D%3A%3D in the body and the
# relay decodes it back — verified 2026-07-21, it is NOT a source of trouble.
MESSAGE_PREFIX = "jarvis_locreq"

# The relay's body when it accepted the message. Anything else is a failure,
# whatever the status code says.
_ACCEPTED = "ok"


def _scrub(text: str) -> str:
    """Remove the key from anything headed for a log line or the database.

    Scrubs the raw value AND its percent-encoded forms. The key travels in an
    `application/x-www-form-urlencoded` body, so a scrubber that only knows the
    literal will happily pass `key%3Dej3j...` straight through — which is how the
    key ended up in a transcript on 2026-07-21.
    """
    key = settings.autoremote_key
    if not key:
        return text
    out = text
    for variant in (key, quote(key, safe=""), quote_plus(key)):
        if variant:
            out = out.replace(variant, "***")
    return out


def _normalized_key() -> str:
    """The key as the relay expects it: the bare token.

    Defensive, and it has already paid for itself. The AutoRemote web page shows
    the key inside a URL query string, so `key=<token>` is the natural thing to
    copy — and that is exactly what was stored in Fly, producing
    `key=key%3D<token>` on the wire and a `NotRegistered` from the relay on every
    single send. Strip the prefix rather than relying on nobody ever pasting it
    again; a config typo should not be able to silently disable the feature.
    """
    key = (settings.autoremote_key or "").strip()
    if key.lower().startswith("key="):
        log.warning("AUTOREMOTE_KEY has a leading 'key=' prefix; stripping it. "
                    "Set the Fly secret to the bare token to silence this.")
        key = key[4:]
    return key


def send(message: str) -> tuple[bool, str | None]:
    """POST a message to the device. Returns `(relay_accepted, error)`.

    `relay_accepted` means THE RELAY TOOK IT — not that the phone received it.
    Nothing on this leg can observe delivery, so the name says only what is known
    (TDD §12). Whether the phone answered is `location_responsiveness`'s job, and
    keeping those separate is the whole attribution argument.

    The caller records both values. A send that failed here is the SERVER's fault
    and must stay distinguishable from a phone that didn't answer.

    Retries once on a connection error only, never on a non-200 or a rejecting
    body: a bad key will be exactly as bad the second time, and a 5xx retried in a
    tick loop is how you build an accidental hammer.
    """
    key = _normalized_key()
    if not key:
        return False, "AUTOREMOTE_KEY is not configured"

    data = {"key": key, "message": message}
    last_err: str | None = None

    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.post(_ENDPOINT, data=data)

            body = _scrub((r.text or "").strip())
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {body[:200]}"
                log.warning("autoremote dispatch failed: %s", last_err)
                return False, last_err

            # 200 is not success here — the body is the outcome.
            if body.lower() != _ACCEPTED:
                last_err = f"relay rejected: {body[:200] or '(empty body)'}"
                log.warning("autoremote dispatch rejected: %s", last_err)
                return False, last_err

            log.info("autoremote dispatch accepted: %s", message)
            return True, None

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
