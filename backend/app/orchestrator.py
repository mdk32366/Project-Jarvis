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
import uuid
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
    * `create_project_from_idea` — creating a NEW GitHub repo from a captured idea.
  To turn an idea into a project: the `secretary` can read the idea (get_idea) and
  list ideas; but YOU call `create_project_from_idea` yourself, behind the gate.
  ASK the user what to name the repo if they didn't say — never invent the name —
  then call it with the idea_id + project_name; the readback + "confirm" create it.
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
- If a compound request has SEVERAL parts, do them all in this one turn: perform
  every ungated action (tasks, docs, sheets, ideas) and raise each gated action
  (emails, invites) so they buffer together. Then read the whole set back as a
  short numbered list — what's already done and what's awaiting confirmation —
  and tell the user a single "confirm" runs all the pending ones (or "cancel"
  drops them). They are one batch; do not make them confirm each separately.
- EVERYTHING ELSE: JUST DO IT. Adding a task, creating a Google Doc or Sheet,
  capturing an idea, saving a contact, or a calendar event with no attendees is
  reversible and needs NO permission. Never ask "would you like me to?" or
  "shall I go ahead?" for these — perform the action, then report what you did.
  The confirmation system will interject on its own for the few actions that
  need it; asking preemptively for anything else is friction, not safety.
- "Set a task", "remind me to", "add to my list" -> add_task. Use create_event
  only for things that belong on the calendar at a specific time.
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


# Words that may accompany a bare yes/no without turning it into a new request.
_CONFIRM_FILLER = {"please", "thanks", "thank", "you", "now", "then", "just",
                   "sure", "it", "that", "one", "and", "the", "go", "ahead"}


def _bare_match(norm: str, vocab: set[str]) -> bool:
    """True only when the message is essentially JUST an affirmative/negative.

    The old check fired on any message STARTING with 'yes' — so 'Yes please run
    it now for part numbers and videos' (a NEW instruction) was read as confirming
    a stale pending action, and a 36-hour-old email got sent (audit). A real
    confirmation is a bare 'yes'/'confirm'/'do it', optionally with harmless
    filler. Anything carrying a content word falls through to normal handling.
    """
    if not norm:
        return False
    if norm in vocab:
        return True
    tokens: set[str] = set()
    for phrase in vocab:
        tokens.update(phrase.split())
    return all(w in tokens or w in _CONFIRM_FILLER for w in norm.split())


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

    # Fix 1 (audit): a confirmation answers something JUST proposed. If the pending
    # has aged past the TTL it's stale — expire it and behave as if none exists, so
    # a later "yes" can't fire a buffered action from hours ago. (awaiting_code has
    # its own tighter code_deadline, enforced in _resolve_awaiting_code.)
    if pending.status == "pending":
        created = pending.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > settings.pending_confirmation_ttl_seconds:
                _expire_stale_pending(db, ctx.thread_key)
                log.info("ignoring stale pending %s (%.0fs old) for thread %s",
                         pending.tool, age, ctx.thread_key)
                return None

    if pending.status == "awaiting_code":
        return _resolve_awaiting_code(db, registry, ctx, pending, user_text)

    affirmative, negative = _vocab(ctx.channel)
    norm = _norm(user_text)
    # Fix 2 (audit): only a BARE affirmative confirms — a message that merely
    # starts with "yes" but carries a new instruction is a new request, not a
    # confirmation of the buffered action.
    if _bare_match(norm, affirmative):
        # Second factor (book_flight) is never batched — it keeps its own TOTP
        # flow. Everything else: one 'confirm' clears the whole batch.
        if pending.tool in _SECOND_FACTOR_TOOLS:
            return _start_second_factor(db, registry, ctx, pending)
        return _execute_batch(db, registry, ctx, pending)

    if _bare_match(norm, negative):
        return _cancel_batch(db, ctx, pending)

    return None  # ambiguous — fall through to normal handling


def _batch_members(db: Session, ctx: Context, pending: PendingConfirmation) -> list[PendingConfirmation]:
    """The still-pending, non-second-factor actions grouped with `pending`.

    A NULL batch_id is a standalone action (just itself). Second-factor tools are
    excluded — they carry their own TOTP flow and must not be batch-executed.
    """
    if pending.batch_id is None:
        return [pending]
    rows = (
        db.execute(
            select(PendingConfirmation)
            .where(PendingConfirmation.thread_key == ctx.thread_key)
            .where(PendingConfirmation.batch_id == pending.batch_id)
            .where(PendingConfirmation.status == "pending")
            .order_by(PendingConfirmation.created_at, PendingConfirmation.id)
        )
        .scalars()
        .all()
    )
    members = [r for r in rows if r.tool not in _SECOND_FACTOR_TOOLS]
    return members or [pending]


def _execute_batch(db: Session, registry: Registry, ctx: Context, pending: PendingConfirmation) -> str:
    """Execute every buffered action in the batch, in creation order, and return a
    single summary of all the deliverables (TDD-multi-action-buffering)."""
    members = _batch_members(db, ctx, pending)
    done: list[tuple[str, str]] = []
    for r in members:
        args = json.loads(r.arguments)
        result = registry.execute(r.tool, args, ctx)
        r.status = "done"
        _audit(db, ctx, r.tool, args, result, "confirmed")
        done.append((r.summary, str(result)))
    db.commit()

    if len(done) == 1:
        return f"Done — {done[0][0]}.\n\n{done[0][1]}"
    lines = [f"Done — {len(done)} actions:"]
    lines += [f"- {summ}" for summ, _ in done]
    lines.append("")
    lines += [res for _, res in done]
    return "\n".join(lines)


def _cancel_batch(db: Session, ctx: Context, pending: PendingConfirmation) -> str:
    members = _batch_members(db, ctx, pending)
    for r in members:
        r.status = "cancelled"
    db.commit()
    if len(members) == 1:
        return f"Cancelled: {members[0].summary}."
    return "Cancelled all pending actions:\n" + "\n".join(f"- {r.summary}" for r in members)


def _expire_stale_pending(db: Session, thread_key: str) -> int:
    """Mark every over-age un-resolved pending on this thread as 'expired', so
    stale buffered actions stop lingering as landmines a future 'yes' could fire."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.pending_confirmation_ttl_seconds)
    rows = (
        db.execute(
            select(PendingConfirmation)
            .where(PendingConfirmation.thread_key == thread_key)
            .where(PendingConfirmation.status == "pending")
        )
        .scalars()
        .all()
    )
    n = 0
    for r in rows:
        created = r.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            r.status = "expired"
            n += 1
    if n:
        db.commit()
    return n


def _start_second_factor(db: Session, registry: Registry, ctx: Context, pending: PendingConfirmation) -> str:
    """The readback cleared with an explicit 'confirm'. Do NOT execute yet —
    verify the offer is still valid, then flip to awaiting_code and ask for
    the TOTP code (flight-booking TDD §2.3).

    Nothing is texted: this is TOTP, so the code already lives on the user's
    enrolled authenticator app. Asking them to read it back is what proves
    possession of the device — a spoofed caller ID cannot produce it.

    ORDERING FIX: pregate runs HERE, not only at execution time. If the offer
    was evicted, expired, or never existed (e.g. a PendingConfirmation whose
    FlightOffer row is gone by the time the user says "confirm"), we refuse
    outright and cancel — never ask for a TOTP code that would be useless.
    The execution-time pregate in _book_flight still runs as defence-in-depth,
    not as a replacement for this check.
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

    # Re-run the pregate check before asking for the TOTP code.
    # Catches: offer expired between the model's book_flight call and the
    # user's "confirm"; offer never existed (phantom PendingConfirmation);
    # booking disabled after the confirmation was queued.
    args = json.loads(pending.arguments)
    pregate_refusal = registry.pregate(pending.tool, args, ctx)
    if pregate_refusal is not None:
        pending.status = "cancelled"
        db.commit()
        log.warning(
            "book_flight second-factor pre-check failed after 'confirm' — cancelling "
            "(thread %s, offer %s): %s",
            ctx.thread_key, args.get("offer_id", "?"), pregate_refusal,
        )
        return f"{pregate_refusal} Nothing was charged."

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
    # One id per turn: gated actions raised in the SAME request are one batch, so
    # a compound "do this, that, and the other" reads back together and clears
    # with a single confirm (TDD-multi-action-buffering).
    batch_id = str(uuid.uuid4())

    for _ in range(_MAX_ITERS):
        resp = create_message(system=system, messages=messages, tools=tools)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if text_parts:
            final_text = "\n".join(text_parts)

        if resp.stop_reason != "tool_use" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": resp.content})

        # Two passes, so NO-CONFIRMATION work always happens FIRST. Pass 1 runs
        # every ungated action (and outright refusals); pass 2 then buffers the
        # gated ones. This guarantees ungated deliverables are executed — and
        # their results in hand — before any gated action is queued for
        # confirmation, rather than depending on the model's tool ordering.
        # Tool results are still emitted in the model's ORIGINAL order (the API
        # matches them by id), so the model sees a coherent transcript.
        contents: dict[str, str] = {}
        gated = []
        for tu in tool_uses:
            pregate_refusal = registry.pregate(tu.name, tu.input, ctx) if registry.has(tu.name) else None
            if pregate_refusal is not None:
                # Refuse outright — never raise a PendingConfirmation for a
                # request that's already invalid (unknown offer_id, booking
                # disabled, an absurd fare). "Confirm or cancel" implies
                # there's something legitimate to confirm; there isn't.
                contents[tu.id] = pregate_refusal
                _audit(db, ctx, tu.name, tu.input, pregate_refusal, "refused")
            elif _needs_confirmation(registry, tu.name, tu.input):
                gated.append(tu)  # buffer AFTER all ungated work below
            else:
                result, status = registry.run_tool(tu.name, tu.input, ctx)
                _audit(db, ctx, tu.name, tu.input, result, status)
                contents[tu.id] = str(result)

        for tu in gated:
            summary = registry.summarize(tu.name, tu.input)
            db.add(
                PendingConfirmation(
                    thread_key=thread_key,
                    channel=channel,
                    tool=tu.name,
                    arguments=json.dumps(tu.input),
                    summary=summary,
                    batch_id=batch_id,
                )
            )
            db.commit()
            contents[tu.id] = (
                f"PENDING_CONFIRMATION: '{summary}'. Ask the user to reply to "
                f"confirm before this is executed. Do not call this tool again."
            )

        results = [{"type": "tool_result", "tool_use_id": tu.id, "content": str(contents[tu.id])}
                   for tu in tool_uses]
        messages.append({"role": "user", "content": results})

    final_text = final_text or "Done."
    add_message(db, convo.id, "assistant", final_text)
    _enqueue_reflect(db, ctx, convo.id)
    return final_text
