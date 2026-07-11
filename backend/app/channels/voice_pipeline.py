"""Voice channel — inbound call -> whitelist -> orchestrator -> spoken reply.

Mirrors sms_pipeline.py, with three structural differences forced by the medium
(see TDD §5, §6):

1. thread_key is the **CallSid**, not the phone number. A call is a bounded
   session; a text thread is continuous. Keying on the number would let a stale
   PendingConfirmation from a previous call be resolved by an unrelated "yes"
   in the next one.

2. The orchestrator is **not** called inline. orchestrator.run() loops up to
   _MAX_ITERS=6 Anthropic calls; Twilio's webhook timeout is ~15s. We return
   TwiML immediately and orchestrate in a background task, parking the result in
   `voice_turns` for the /poll route to collect.

3. Confirmation vocabulary is **narrowed**, not widened. On a phone line "ok"
   and "yeah" are conversational filler and are unsafe triggers for a gated
   action. Voice requires an explicit token.

Phase 1 is READ-ONLY. Voice auth is caller-ID, which is spoofable — materially
weaker than the SMS/email whitelist. No mutating tool is reachable from here.
See TDD §3.
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import normalize_number, settings
from app.jobs import enqueue
from app.models import ContactWhitelist, VoiceTurn
from app.orchestrator import run as orchestrate

log = logging.getLogger(__name__)

CHANNEL = "voice"

# ── Phase 1 allowlists (TDD §3.3) ────────────────────────────────────────────
# The top-level orchestrator registry is a PURE DELEGATOR: it holds `delegate`
# and `trade`, nothing else. Every domain tool lives in the sub-agent registry
# and is reached THROUGH `delegate`. So:
#
#   * `delegate` MUST be allowlisted — without it voice has no tools at all.
#   * The tool allowlist's real job at top level is dropping `trade`.
#   * The meaningful restriction is WHICH AGENTS voice may delegate to, enforced
#     in agents._delegate (see PATCHES §2b).
#
# Allowlist, NOT denylist. Fail closed.
VOICE_TOOLS_PHASE1: set[str] = {
    "delegate",  # required — the only route to any tool
    "get_node_status",
    "get_service_health",
    "remember_fact",
    "fleet_health",
    "fleet_spend",
}

# Which specialists voice may reach. `_delegate` also re-validates each agent's
# LIVE DB roster against VOICE_TOOLS_PHASE1 at call time, so editing an agent in
# the admin UI cannot silently widen what a phone call can do.
VOICE_AGENTS_PHASE1: set[str] = {"netstatus", "infra", "archivist", "researcher"}

# ── Confirmation vocabulary (TDD §8.2) ───────────────────────────────────────
# Deliberately NARROWER than the orchestrator's typed-text sets. Anything not
# listed here is ambiguous and falls through, causing JARVIS to re-ask.
VOICE_AFFIRMATIVE = {"confirm", "confirmed", "affirmative", "execute", "roger", "do it"}
VOICE_NEGATIVE = {"negative", "cancel", "abort", "belay", "no"}

# ── Poll budget (TDD §6.2) ───────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 2
MAX_POLLS = 8  # ~16s of orchestration before we bail to email
MAX_TURNS = 40  # hard stop on call length

GREETING = "JARVIS here. What do you need?"
FILLER = "Working on it."
TIMEOUT_FALLBACK = (
    "That's taking longer than I can hold the line for. "
    "I'll email you the answer. Anything else?"
)
NOT_AUTHORIZED = "I'm not able to help with that. Goodbye."


# ── Whitelist ────────────────────────────────────────────────────────────────
def is_allowed(db: Session, number: str) -> bool:
    """Same logic as SMS, scoped to the voice channel.

    NOTE: for SMS the From header is carrier-attested. For VOICE it is caller
    ID, which is trivially spoofable. This function is a speed bump on voice,
    not an authentication boundary. It is why Phase 1 exposes no writes.
    """
    num = normalize_number(number)
    if num and num in settings.allowed_number_list:
        return True
    rows = (
        db.execute(select(ContactWhitelist).where(ContactWhitelist.channel == CHANNEL))
        .scalars()
        .all()
    )
    return any(normalize_number(r.identifier) == num for r in rows)


# ── Turn store ───────────────────────────────────────────────────────────────
def open_turn(db: Session, call_sid: str, turn: int, user_text: str) -> VoiceTurn:
    row = VoiceTurn(call_sid=call_sid, turn=turn, status="pending", user_text=user_text)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_turn(db: Session, call_sid: str, turn: int) -> VoiceTurn | None:
    return (
        db.execute(
            select(VoiceTurn)
            .where(VoiceTurn.call_sid == call_sid)
            .where(VoiceTurn.turn == turn)
        )
        .scalars()
        .first()
    )


def run_turn(db: Session, call_sid: str, turn: int, from_number: str, user_text: str) -> None:
    """Background: orchestrate one turn and park the result.

    Runs OUTSIDE the webhook request/response cycle. Must never raise into the
    caller — a failure here becomes a spoken apology, not a 500.
    """
    row = get_turn(db, call_sid, turn)
    if row is None:
        log.error("run_turn: no row for %s/%s", call_sid, turn)
        return
    try:
        reply = orchestrate(
            db=db,
            channel=CHANNEL,
            thread_key=call_sid,  # per-call scoping — see module docstring
            user_text=user_text,
            actor=from_number,
        )
        row.reply = reply or "Done."
        row.status = "done"
        db.commit()
    except Exception as e:  # noqa: BLE001 — a dropped call is worse than a logged error
        log.exception("voice turn failed: %s/%s", call_sid, turn)
        row.status = "error"
        row.error = str(e)[:2000]
        db.commit()


def email_transcript(db: Session, call_sid: str, from_number: str) -> None:
    """Queue an emailed transcript of the call.

    Spoken replies vanish; this is the durable audit trail. Uses the job queue
    (correct here — not latency-sensitive), unlike the orchestration step.
    """
    if not settings.owner_email_resolved:
        return
    rows = (
        db.execute(
            select(VoiceTurn)
            .where(VoiceTurn.call_sid == call_sid)
            .order_by(VoiceTurn.turn)
        )
        .scalars()
        .all()
    )
    if not rows:
        return
    lines: list[str] = []
    for r in rows:
        lines.append(f"You: {r.user_text or ''}")
        lines.append(f"JARVIS: {r.reply or ('[error] ' + (r.error or '')) or '[no reply]'}")
        lines.append("")
    enqueue(
        db,
        "email_copy",
        {
            "to": settings.owner_email_resolved,
            "subject": f"JARVIS (call transcript {call_sid[-8:]})",
            "body": "\n".join(lines),
        },
        channel=CHANNEL,
        thread_key=call_sid,
        actor=from_number,
    )


# ── TwiML builders ───────────────────────────────────────────────────────────
_XML = '<?xml version="1.0" encoding="UTF-8"?>'


def _say(text: str) -> str:
    # escape() handles & < > but NOT quotes unless given an entity map. The reply
    # is LLM output — it will contain quoted hostnames. Escape them too.
    body = escape(text, {'"': "&quot;", "'": "&apos;"})
    return f'<Say voice="{settings.voice_tts_voice}">{body}</Say>' 


def twiml_gather(prompt: str, turn: int, action: str = "/api/voice/gather") -> str:
    """Speak `prompt`, then listen. `turn` rides in the action URL as state."""
    return (
        f"{_XML}<Response>"
        f'<Gather input="speech" language="en-US" speechTimeout="auto" '
        f'method="POST" action="{action}?turn={turn}">'
        f"{_say(prompt)}"
        f"</Gather>"
        # Fallthrough: caller said nothing.
        f"{_say('Still there?')}"
        f'<Redirect method="POST">{action}?turn={turn}</Redirect>'
        f"</Response>"
    )


def twiml_working(call_sid: str, turn: int, poll: int) -> str:
    """Return immediately, then bounce to /poll. Alternate speech and silence so
    the filler doesn't become grating (TDD §6.2)."""
    body = _say(FILLER) if poll == 0 else '<Pause length="2"/>'
    return (
        f"{_XML}<Response>"
        f"{body}"
        f'<Redirect method="POST">/api/voice/poll?turn={turn}&amp;poll={poll + 1}</Redirect>'
        f"</Response>"
    )


def twiml_hangup(message: str) -> str:
    return f"{_XML}<Response>{_say(message)}<Hangup/></Response>"


def twiml_empty() -> str:
    return f"{_XML}<Response></Response>"
