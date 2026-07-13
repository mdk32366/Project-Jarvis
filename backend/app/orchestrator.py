"""The JARVIS core loop: route a request, run tools, enforce the gate, reply.

The intent "router" is realized by giving Claude the full tool set and the
persona/preference context, then letting it choose tools (and answer directly
for plain Q&A).

Phase 1 additions:
  * the tool registry is built per-request so runtime flags (e.g. ENABLE_TRADING)
    take effect without a restart;
  * after each turn a `reflect` job is enqueued so the memory reflector learns
    durable facts out-of-band (see app/reflector.py, app/jobs.py).
Explicit multi-agent delegation remains future work.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import totp
from app.config import settings
from app.handlers.base import Context, Registry, build_registry
from app.jobs import enqueue
from app.llm import create_message
from app.memory import add_message, build_system_preamble, get_or_create_conversation, load_history
from app.models import ActionAudit, PendingConfirmation

# Gated tools that require a SECOND factor (a TOTP code) after the normal
# "confirm" clears, before they execute — flight-booking TDD §2.3. The gate
# proves the caller said the word; the code proves they hold the enrolled
# device, which is what actually survives a spoofed caller ID. Currently just
# book_flight, but kept as a set rather than a single name check in case a
# future irreversible-and-spends-money tool needs the same treatment.
_SECOND_FACTOR_TOOLS = {"book_flight"}

log = logging.getLogger(__name__)

_MAX_ITERS = 6

_AFFIRMATIVE = {"yes", "y", "yep", "yeah", "confirm", "confirmed", "do it", "go ahead", "proceed", "ok", "okay"}
_NEGATIVE = {"no", "n", "nope", "cancel", "stop", "don't", "dont", "abort"}

# Channel-specific confirmation vocabulary (TDD 8.2). On a phone line "ok" and
# "yeah" are conversational filler and are UNSAFE triggers for a gated action —
# STT will happily transcribe an idle "yeah..." mid-sentence. For voice we
# NARROW rather than widen: an explicit token is required, and anything else is
# ambiguous and falls through, causing JARVIS to re-ask. That is the point.
_VOCAB = {
    "voice": (
        {"confirm", "confirmed", "affirmative", "execute", "roger", "do it"},
        {"negative", "cancel", "abort", "belay", "no"},
    ),
}


def _vocab(channel: str) -> tuple[set[str], set[str]]:
    return _VOCAB.get(channel, (_AFFIRMATIVE, _NEGATIVE))

_INSTRUCTIONS = """
## Operating instructions
- You are the orchestrator. Answer general conversation and simple questions
  directly. For anything needing a specialist capability, hand it off with the
  `delegate` tool and then synthesize the result for the user:
    * market data (stock prices, portfolio)  -> agent "finance"
    * saving durable facts about the user     -> agent "archivist"
    * focused research, analysis, or drafting -> agent "researcher"
  You may delegate more than once and combine the results.
- SOME ACTIONS ARE HANDLED HERE, NOT DELEGATED, because they are irreversible
  and must stay under the confirmation gate. Sub-agents CANNOT run them:
    * `send_email`   — sending mail as the user
    * `create_event` — writing to their real calendar (and emailing attendees)
    * `place_stock_order` — trading
    * `book_flight`  — buying a plane ticket. SPENDS REAL MONEY.
  The `secretary` agent can DRAFT an email (draft_email) but cannot send one.
  When the user approves a draft, YOU call `send_email` yourself with the full
  to/subject/body. Do NOT delegate the send, and do NOT tell the user you are
  unable to send — you can.
  The `travel` agent can SEARCH flights (search_flights) but cannot book. When
  the user wants to book one of the offers it found, YOU call `book_flight`
  yourself with that offer_id — never one you invented or one described to you
  outside a search_flights result. Booking requires the user's "confirm" AND
  then a TOTP code read from their authenticator app before anything is
  charged; after "confirm" ask for the code — do not call book_flight again
  yourself, the code is verified by the confirmation system, not a tool call.
- When a tool result says PENDING_CONFIRMATION, tell the user what you intend to
  do and ask them to reply to confirm. Do not retry the tool yourself.
- Be concise; lead with the answer. Match the user's tone and the standing
  preferences above.
"""

_VOICE_INSTRUCTIONS = """
## You are being SPOKEN ALOUD
Everything you write is fed to a text-to-speech engine and read to someone on a
phone. Write for an ear, not a screen.

- **NEVER use markdown.** No tables, no `|`, no `---`, no `**bold**`, no `#`
  headers, no bullet characters, no numbered lists with `1.` on its own line.
  A table renders as "horizontal line, horizontal line, horizontal line" — the
  TTS reads the pipes and dashes out loud, literally.
- Write flowing sentences. To list options, say them: "Two options. First,
  Alaska at three seventeen, departing seven oh four in the morning, one stop.
  Second, Duffel at one oh two, but it leaves at two nineteen a.m."
- Round and simplify. "About three hundred" beats "$317.00". Say "seven oh four
  in the morning", not "07:04".
- Be SHORT. A caller cannot skim. Lead with the answer, give two or three
  options at most, and stop. Offer detail rather than reciting it.
- Never read a URL, an ID, or a hash aloud unless asked. Say "I'll email you
  the link."

## You can CALL THEM BACK
If something will take longer than a caller will sit through, do NOT demote it to
an email. Use `call_me_back`: say you'll ring back, hang up, do the work, and
ring. That is what an assistant does; emailing someone the thing they just asked
you for out loud is what an IVR does.

Write the `opening` as the actual sentence you'll speak when they pick up — they
have no idea why you're calling, so lead with it. Not "Regarding your query" but
"It's JARVIS. I've got those flight results."
"""


def _norm(text: str) -> str:
    return text.strip().lower().rstrip(".!")


def _audit(db: Session, ctx: Context, tool: str, args: dict, result: str, status: str) -> None:
    db.add(
        ActionAudit(
            channel=ctx.channel,
            actor=ctx.actor,
            tool=tool,
            arguments=json.dumps(args)[:4000],
            result=str(result)[:4000],
            status=status,
        )
    )
    db.commit()


def _needs_confirmation(registry: Registry, name: str, args: dict) -> bool:
    """Gated tools require confirmation when the amount is unknown or >= threshold."""
    if not registry.is_gated(name):
        return False
    notional = registry.notional(name, args)
    if notional is None:
        return True
    return notional >= settings.confirm_threshold_usd


def _resolve_pending(db: Session, registry: Registry, ctx: Context, user_text: str) -> str | None:
    """If a confirmation is pending for this thread, act on yes/no. Returns reply or None."""
    pending = (
        db.execute(
            select(PendingConfirmation)
            .where(PendingConfirmation.thread_key == ctx.thread_key)
            .where(PendingConfirmation.status.in_(("pending", "awaiting_code")))
            .order_by(PendingConfirmation.created_at.desc())
        )
        .scalars()
        .first()
    )
    if pending is None:
        return None

    if pending.status == "awaiting_code":
        return _resolve_awaiting_code(db, registry, ctx, pending, user_text)

    affirmative, negative = _vocab(ctx.channel)
    norm = _norm(user_text)
    if norm in affirmative or any(norm.startswith(a + " ") for a in affirmative):
        if pending.tool in _SECOND_FACTOR_TOOLS:
            return _start_second_factor(db, ctx, pending)
        args = json.loads(pending.arguments)
        result = registry.execute(pending.tool, args, ctx)
        pending.status = "done"
        db.commit()
        _audit(db, ctx, pending.tool, args, result, "confirmed")
        return f"Done — {pending.summary}.\n\n{result}"

    if norm in negative or any(norm.startswith(n + " ") for n in negative):
        pending.status = "cancelled"
        db.commit()
        return f"Cancelled: {pending.summary}."

    return None  # ambiguous — fall through to normal handling


def _start_second_factor(db: Session, ctx: Context, pending: PendingConfirmation) -> str:
    """The readback cleared with an explicit 'confirm'. Do NOT execute yet —
    flip to awaiting_code and ask for the TOTP code (flight-booking TDD §2.3).

    Nothing is texted: this is TOTP, so the code already lives on the user's
    enrolled authenticator app. Asking them to read it back is what proves
    possession of the device — a spoofed caller ID cannot produce it.
    """
    if not totp.totp_configured():
        # Fail closed: no second factor configured means booking cannot be
        # authorized at all, not that the check is skipped. TDD §8: "do not
        # skip the second factor because the gate exists."
        pending.status = "cancelled"
        db.commit()
        log.error("book_flight confirmed but TOTP_SECRET is not configured — refusing")
        return (
            "I can't complete this — the booking second factor (TOTP) isn't "
            "configured on this instance, so I won't book without it. Nothing "
            "was charged."
        )
    pending.status = "awaiting_code"
    pending.code_deadline = datetime.now(timezone.utc) + timedelta(seconds=settings.booking_code_ttl_seconds)
    pending.code_attempts = 0
    db.commit()
    return (
        f"Readback confirmed: {pending.summary}. Read me the code from your "
        f"authenticator app to finish booking."
    )


def _resolve_awaiting_code(
    db: Session, registry: Registry, ctx: Context, pending: PendingConfirmation, user_text: str
) -> str:
    """Verify a TOTP code against an awaiting_code row. Three attempts, then
    CANCEL — not "try again" (TDD §2.3: unlimited retries turn a 6-digit code
    into a brute-force oracle). A code window that never expires is a
    password, hence the hard deadline check before anything else."""
    now = datetime.now(timezone.utc)
    deadline = pending.code_deadline
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)  # sqlite loses tzinfo on round-trip
    if deadline is not None and now > deadline:
        pending.status = "cancelled"
        db.commit()
        return f"That confirmation expired before a code was entered. Cancelled: {pending.summary}."

    # Let an explicit cancel escape the code prompt too.
    _, negative = _vocab(ctx.channel)
    norm = _norm(user_text)
    if norm in negative or any(norm.startswith(n + " ") for n in negative):
        pending.status = "cancelled"
        db.commit()
        return f"Cancelled: {pending.summary}."

    if totp.verify(user_text):
        args = json.loads(pending.arguments)
        result = registry.execute(pending.tool, args, ctx)
        pending.status = "done"
        db.commit()
        _audit(db, ctx, pending.tool, args, result, "confirmed")
        return f"Confirmed — {pending.summary}.\n\n{result}"

    pending.code_attempts = (pending.code_attempts or 0) + 1
    if pending.code_attempts >= settings.booking_code_max_attempts:
        pending.status = "cancelled"
        db.commit()
        log.warning("book_flight: code failed %d times — cancelling (thread %s)",
                    pending.code_attempts, ctx.thread_key)
        return f"That code didn't match, and that was the last attempt. Cancelled: {pending.summary}."

    db.commit()
    remaining = settings.booking_code_max_attempts - pending.code_attempts
    return f"That code didn't match. {remaining} attempt(s) left — try again."


def _enqueue_reflect(db: Session, ctx: Context, convo_id: int) -> None:
    if not settings.enable_reflector:
        return
    try:
        enqueue(db, "reflect", {"conversation_id": convo_id},
                channel=ctx.channel, thread_key=ctx.thread_key, actor=ctx.actor)
    except Exception as e:  # never let bookkeeping break a reply
        log.warning("could not enqueue reflect job: %s", e)


def run(db: Session, channel: str, thread_key: str, user_text: str, actor: str, subject: str = "") -> str:
    """Process one inbound message and return JARVIS's reply text."""
    # Voice: restrict the top-level registry. NOTE `delegate` MUST stay in the
    # allowlist — the top-level registry is a pure delegator and it is voice's
    # only route to any tool at all. The allowlist's real job here is dropping
    # place_stock_order.
    allow = None
    if channel == "voice":
        from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
        allow = VOICE_TOOLS_PHASE1
    registry = build_registry(include_delegate=True, db=db, allow=allow)  # top-level: honors flags + live agent roster
    convo = get_or_create_conversation(db, channel, thread_key, subject)
    ctx = Context(db=db, channel=channel, actor=actor, thread_key=thread_key)

    add_message(db, convo.id, "user", user_text)

    # 1) Resolve an outstanding confirmation first.
    resolved = _resolve_pending(db, registry, ctx, user_text)
    if resolved is not None:
        add_message(db, convo.id, "assistant", resolved)
        _enqueue_reflect(db, ctx, convo.id)
        return resolved

    # 2) Normal handling: persona + preferences + history + tool loop.
    system = build_system_preamble(db, query=user_text) + "\n" + _INSTRUCTIONS
    if channel == "voice":
        system += "\n" + _VOICE_INSTRUCTIONS
    messages = load_history(db, convo.id)
    tools = registry.anthropic_tools()
    final_text = ""

    for _ in range(_MAX_ITERS):
        resp = create_message(system=system, messages=messages, tools=tools)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if text_parts:
            final_text = "\n".join(text_parts)

        if resp.stop_reason != "tool_use" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            pregate_refusal = registry.pregate(tu.name, tu.input, ctx) if registry.has(tu.name) else None
            if pregate_refusal is not None:
                # Refuse outright — never raise a PendingConfirmation for a
                # request that's already invalid (unknown offer_id, booking
                # disabled, an absurd fare). "Confirm or cancel" implies
                # there's something legitimate to confirm; there isn't.
                content = pregate_refusal
                _audit(db, ctx, tu.name, tu.input, content, "refused")
            elif _needs_confirmation(registry, tu.name, tu.input):
                summary = registry.summarize(tu.name, tu.input)
                db.add(
                    PendingConfirmation(
                        thread_key=thread_key,
                        channel=channel,
                        tool=tu.name,
                        arguments=json.dumps(tu.input),
                        summary=summary,
                    )
                )
                db.commit()
                content = (
                    f"PENDING_CONFIRMATION: '{summary}'. Ask the user to reply to "
                    f"confirm before this is executed. Do not call this tool again."
                )
            else:
                content = registry.execute(tu.name, tu.input, ctx)
                _audit(db, ctx, tu.name, tu.input, content, "ok")

            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(content)})

        messages.append({"role": "user", "content": results})

    final_text = final_text or "Done."
    add_message(db, convo.id, "assistant", final_text)
    _enqueue_reflect(db, ctx, convo.id)
    return final_text
