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
import re
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
    # ungated, universal, no side effects — available everywhere (TDD #11 §4.1)
    "get_current_datetime",
    "self_whoami",  # JARVIS's own provenance + health ("how are you feeling")
    # netstatus
    "get_node_status",
    "get_service_health",
    # infra (real Fly data)
    "fleet_health",
    "fleet_spend",
    # archivist — she must be able to FORGET, not just remember. A wrong belief
    # that can't be corrected is worse than no memory at all.
    "remember_fact",
    "recall_facts",
    "forget_fact",
    "audit_memory",
    # episodic memory (TDD #14) — read-only recall of past conversations, plus
    # the same correction right the fact store has. NOTE: every archivist tool
    # must appear here or the roster-subset check silently removes the whole
    # agent from voice.
    "recall_episodes",
    "recall",
    "forget_episode",
    # researcher: she can find out instead of guessing — and can say when she's
    # guessing, which is arguably worth more.
    "web_search",
    "fetch_page",
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
    "get_idea",
    # create_project_from_idea: GATED + top-level (creates a GitHub repo). Voice-
    # reachable behind the confirmation gate, same posture as send_email — it's an
    # outward action, not money, and a private repo is easily undone.
    "create_project_from_idea",
    # Project tracking (TDD project-tracking §2 goal 5): "where am I on X" is a
    # question asked from a boat, so it has to work on a phone call. All ungated
    # and reversible bookkeeping — no money, no outward message, nothing that
    # can't be corrected by saying the opposite. NOTE: every secretary tool must
    # appear here or the roster-subset check silently drops the WHOLE agent from
    # voice.
    "create_project",
    "promote_idea",
    "list_projects",
    "project_status",
    "add_milestone",
    "complete_milestone",
    "drop_milestone",
    "set_project_status",
    "attach_document",
    "supersede_document",
    # travel — read, booking, and document creation.
    "list_trips",
    "search_flights",
    # Google Docs/Sheets: ungated (TDD #13 §4). Voice can create a trip itinerary
    # or summary doc on request. append_to_google_doc ownership-scoped same as offer_id.
    "create_google_doc",
    "create_google_sheet",
    "append_to_google_doc",
    # book_flight: GATED + SECOND FACTOR (flight-booking TDD §2.4 — decided).
    # Voice CAN book, unlike place_stock_order, specifically because the TOTP
    # code is the one control that beats caller-ID spoofing: a spoofed caller
    # cannot produce it. Without the second factor this would stay excluded,
    # same as place_stock_order.
    "book_flight",
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
    # navigator: the daily-use one. "What time do I need to leave?"
    "get_traffic",
    "find_place",
    "where_am_i",
    # tailnet
    "tailscale_status",
    # watches: she acts while you're not thinking about her
    "watch_for",
    "list_watches",
    "cancel_watch",
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
    "navigator",   # traffic, leave-by times, places
}

# ── Confirmation vocabulary (TDD §8.2) ───────────────────────────────────────
# The voice confirmation vocabulary is NARROWER than the orchestrator's
# typed-text sets — "ok"/"yeah" must never fire a gated action. The single
# source of truth is orchestrator._VOCAB["voice"], consumed by the gate. A
# duplicate copy used to live here under this banner but was never read;
# removed in the 2026-07-17 audit (audit M8) so edits land where they take
# effect.

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
    "This one's taking a bit. I can hold the line while I finish — just say wait — "
    "or I can email it to you when it's ready. Which would you rather?"
)
HOLD_INTRO = "You got it. Stay with me — I'll come right back the moment I have it."
HOLD_REASSURE = "Still on it."
# Said when she gives up holding and hands off to email (the user's own words).
HANDOFF_LINE = (
    "This is taking longer than I expected. I'll email you the full answer the "
    "moment it's done and it'll land in your inbox automatically. Catch you in a bit."
)
NOT_AUTHORIZED = "I'm not able to help with that. Goodbye."

# The "wait or email" choice at the timeout prompt. An explicit request to be
# emailed/called hands off; ANYTHING ELSE (including "wait", "keep going", or
# unrecognized speech) holds — because a turn is still running and holding is
# what stops the caller being made to talk into a re-prompt loop.
_CALLBACK_WORDS = ("call back", "call me back", "callback", "email", "e-mail",
                   "hang up", "text me", "later")


def wants_callback(speech: str) -> bool:
    s = (speech or "").strip().lower()
    return any(w in s for w in _CALLBACK_WORDS)


def wants_to_hold(speech: str) -> bool:
    """True to keep holding — explicitly, or by default when it's not a callback."""
    if wants_callback(speech):
        return False
    return True


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
            # If the caller stopped waiting for this one (held past the budget or
            # asked to be emailed), deliver the finished answer instead of letting
            # it evaporate. Re-read: notify_email may have been set concurrently.
            row = get_turn(db, call_sid, turn)
            if row is not None and row.notify_email:
                _enqueue_answer_email(db, from_number, row.reply)
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

# _VOICE_INSTRUCTIONS already tells the model never to read a URL aloud, but a
# prompt is a request, not a guarantee — when the model quotes an email body or
# research verbatim, links come with it and the TTS dutifully spells out every
# character of a 200-char tracking URL. Sanitize deterministically at the last
# exit before <Say>, where nothing can slip past. Transcripts and the DB keep
# the full reply; only the spoken audio is filtered.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")            # [text](url) -> text
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)


def _speakable(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("a link", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _say(text: str) -> str:
    # escape() handles & < > but NOT quotes unless given an entity map. The reply
    # is LLM output — it will contain quoted hostnames. Escape them too.
    body = escape(_speakable(text), {'"': "&quot;", "'": "&apos;"})
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


def twiml_hold(turn: int, since: int, intro: bool = False) -> str:
    """Hold the line on a still-running turn: play music (or reassure), then poll.

    CRUCIAL: there is NO <Gather> here. The caller chose to wait, so we never
    listen — silence can't fire a "Still there?" re-prompt, which is the loop
    that used to form when a quiet, waiting caller was treated as needing another
    prompt. We just keep the line warm and bounce back to /voice/hold, which
    checks whether the answer is ready.
    """
    parts = []
    if intro:
        parts.append(_say(HOLD_INTRO))
    if settings.voice_hold_music_url:
        # One pass = one play of the track, then re-check. escape() guards the URL.
        parts.append(f"<Play>{escape(settings.voice_hold_music_url)}</Play>")
    else:
        # No music configured: a short spoken 'still on it' on entry, then quiet
        # pauses. Recommend configuring voice_hold_music_url for a nicer hold.
        parts.append(_say(HOLD_REASSURE) if intro else '<Pause length="8"/>')
    action = f"/api/voice/hold?turn={turn}&amp;since={since}"
    parts.append(f'<Redirect method="POST">{action}</Redirect>')
    return f"{_XML}<Response>{''.join(parts)}</Response>"


def twiml_hangup(message: str) -> str:
    return f"{_XML}<Response>{_say(message)}<Hangup/></Response>"


def twiml_empty() -> str:
    return f"{_XML}<Response></Response>"


def mark_notify_on_completion(db: Session, call_sid: str, turn: int, from_number: str) -> None:
    """Arrange for a still-running turn's answer to be emailed when it finishes.

    Called when the caller stops waiting (held past the budget, or asked to be
    emailed). If the turn already finished in the race window, email it now;
    otherwise flag it so run_turn emails on completion.
    """
    row = get_turn(db, call_sid, turn)
    if row is None:
        return
    if row.status == "done" and row.reply:
        _enqueue_answer_email(db, from_number, row.reply)
        return
    row.notify_email = True
    db.commit()


def _enqueue_answer_email(db: Session, from_number: str, reply: str) -> None:
    """Queue the finished answer to the owner's inbox (best-effort, durable)."""
    to = settings.owner_email_resolved
    if not to:
        return
    enqueue(
        db,
        "email_copy",
        {"to": to, "subject": "JARVIS — the answer you asked me to finish", "body": reply},
        channel=CHANNEL,
        actor=from_number,
    )
