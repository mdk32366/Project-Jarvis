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
    # netstatus
    "get_node_status",
    "get_service_health",
    # infra (real Fly data)
    "fleet_health",
    "fleet_spend",
    # archivist
    "remember_fact",
    # finance — READ ONLY. place_stock_order is NOT here and is not reachable:
    # it lives on the top-level registry behind the gate, and the finance agent's
    # roster does not include it.
    "get_stock_price",
    "get_portfolio",
    # scheduling — read. create_event is gated and top-level only.
    "calendar_lookup",
    # secretary — tasks, ideas, and DRAFTING email.
    "draft_email",
    # GATED ACTIONS. These are irreversible, and they are reachable from voice
    # ONLY because the confirmation gate genuinely works: JARVIS reads the action
    # back, and nothing executes until an explicit "confirm" / "affirmative".
    # Note that voice deliberately does NOT accept "ok" or "yeah" for these
    # (orchestrator._VOCAB) — conversational filler must never fire an
    # irreversible action.
    #
    # They were originally withheld because Phase 1 voice was read-only. But the
    # gate is the real control; keeping them out of the allowlist as well just
    # meant JARVIS truthfully told the user she couldn't send an email she was
    # otherwise fully equipped to send.
    #
    # place_stock_order stays OUT. Spending money on a spoofable channel is a
    # different risk class from sending a mail or booking a meeting.
    "send_email",
    "create_event",
    "add_task",
    "list_tasks",
    "complete_task",
    "cancel_task",
    "capture_idea",
    "list_ideas",
    # travel — read only; JARVIS cannot book.
    "list_trips",
    "search_flights",
    # identity + address book. `whoami` is why she stops asking the owner for
    # their own email address after emailing them a transcript every single call.
    "whoami",
    "lookup_contact",
    "save_contact",
    "list_contacts",
    "sync_google_contacts",
    "google_status",
    # She can ring back. This is what stops "I'll email you" being the answer to
    # every slow request.
    "call_me_back",
    "pending_callbacks",
    "cancel_callback",
}

# Which specialists voice may reach. `_delegate` also re-validates each agent's
# LIVE DB roster against VOICE_TOOLS_PHASE1 at call time, so editing an agent in
# the admin UI cannot silently widen what a phone call can do.
VOICE_AGENTS_PHASE1: set[str] = {
    "netstatus", "infra", "archivist", "researcher",
    "finance",     # prices/portfolio only — the agent cannot place orders
    "scheduling",  # calendar read
    "secretary",   # tasks, ideas, email DRAFTS
    "travel",      # booked trips + flight research
}

# ── Confirmation vocabulary (TDD §8.2) ───────────────────────────────────────
# Deliberately NARROWER than the orchestrator's typed-text sets. Anything not
# listed here is ambiguous and falls through, causing JARVIS to re-ask.
VOICE_AFFIRMATIVE = {"confirm", "confirmed", "affirmative", "execute", "roger", "do it"}
VOICE_NEGATIVE = {"negative", "cancel", "abort", "belay", "no"}

# ── Poll budget (TDD §6.2) ───────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 2
# Real orchestration on this app takes 20-35s: several Anthropic round-trips plus
# a `delegate` hop into a sub-agent (which runs its OWN tool loop). The original
# 8 polls (~16s) was exhausted on nearly every substantive turn, which is what
# produced both the constant "I'll email you" AND the [error]s: the abandoned
# background task kept running and collided with the next turn.
MAX_POLLS = 20  # ~40s
MAX_TURNS = 40  # hard stop on call length

GREETING = "JARVIS here. What do you need?"
FILLER = "Copy that."
TIMEOUT_FALLBACK = (
    "Still working on that one. I can call you back with it, or keep going here — "
    "which would you rather?"
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


def seed_context(db: Session, call_sid: str, context: str) -> None:
    """Seed an OUTBOUND call's conversation with why JARVIS rang.

    Without this she'd open with "I've got those flight results" and then, the
    moment the user says "go on", have no idea what she was talking about — the
    opening line is TwiML, not conversation history. This writes the reason into
    the conversation so the first real turn has context.
    """
    from app.memory import add_message, get_or_create_conversation

    if not call_sid or not context:
        return
    convo = get_or_create_conversation(db, CHANNEL, call_sid, "")
    add_message(db, convo.id, "assistant",
                f"(I placed this call. Reason: {context})")


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


def prior_turn_still_running(db: Session, call_sid: str, turn: int) -> bool:
    """Is an EARLIER turn of this call still orchestrating?

    THE [error] BUG. The poll budget would expire while the background task kept
    running. The caller, hearing "I'll email you," would speak again — starting
    turn N+1 while turn N was still mid-orchestration. Two run_turn calls then
    shared one CallSid (= one thread_key = one conversation row) and collided.
    The loser raised, and its turn was recorded as [error].

    Every [error] in the transcripts is immediately preceded by a "poll budget
    exhausted" for the PRIOR turn. That is the signature.
    """
    return (
        db.execute(
            select(VoiceTurn)
            .where(VoiceTurn.call_sid == call_sid)
            .where(VoiceTurn.turn < turn)
            .where(VoiceTurn.status == "pending")
        )
        .scalars()
        .first()
        is not None
    )


def run_turn(call_sid: str, turn: int, from_number: str, user_text: str) -> None:
    """Background: orchestrate one turn and park the result.

    OPENS ITS OWN DB SESSION. This is not optional.

    FastAPI's Depends(get_db) yields a REQUEST-SCOPED session and closes it in a
    `finally` the moment the response is sent. A BackgroundTask runs AFTER that.
    Passing the request's session in here meant the orchestrator was writing
    through a closed session — which sometimes won the race and sometimes didn't.

    That was the `JARVIS: [error]` at the end of every call: the last turn, still
    orchestrating as the request finished, reliably lost the race.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
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
            db.rollback()
            row = get_turn(db, call_sid, turn)
            if row is not None:
                row.status = "error"
                row.error = str(e)[:2000]
                db.commit()
    finally:
        db.close()


_transcript_sent: set[str] = set()


def email_transcript(db: Session, call_sid: str, from_number: str) -> None:
    """Queue ONE emailed transcript per call.

    Spoken replies vanish; this is the durable audit trail. Uses the job queue
    (correct here — not latency-sensitive), unlike the orchestration step.

    DEDUPED. There are four call sites (max turns, "goodbye", poll-budget
    exhaustion, and the hangup status callback) and a single call can trip
    several of them — a call with two slow turns plus a hangup emailed THREE
    transcripts, each a superset of the last.

    The guard is the DB, not the in-process set: `api` may be multi-machine and
    the set is per-process. The set is only a cheap fast path.
    """
    if not settings.owner_email_resolved:
        return
    if call_sid in _transcript_sent:
        return

    # Authoritative check: has a transcript job for this call already been queued?
    from app.models import Job

    dupe = (
        db.execute(
            select(Job)
            .where(Job.kind == "email_copy")
            .where(Job.thread_key == call_sid)
        )
        .scalars()
        .first()
    )
    if dupe is not None:
        _transcript_sent.add(call_sid)
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
    _transcript_sent.add(call_sid)


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
