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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.handlers.base import Context, Registry, build_registry
from app.jobs import enqueue
from app.llm import create_message
from app.memory import add_message, build_system_preamble, get_or_create_conversation, load_history
from app.models import ActionAudit, PendingConfirmation

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
- Trading is handled here (not delegated) so it stays under the confirmation
  gate. When a tool result says PENDING_CONFIRMATION, tell the user what you
  intend to do and ask them to reply to confirm. Do not retry the tool yourself.
- Be concise; lead with the answer. Match the user's tone and the standing
  preferences above.
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
            .where(PendingConfirmation.status == "pending")
            .order_by(PendingConfirmation.created_at.desc())
        )
        .scalars()
        .first()
    )
    if pending is None:
        return None

    affirmative, negative = _vocab(ctx.channel)
    norm = _norm(user_text)
    if norm in affirmative or any(norm.startswith(a + " ") for a in affirmative):
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
            if _needs_confirmation(registry, tu.name, tu.input):
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
