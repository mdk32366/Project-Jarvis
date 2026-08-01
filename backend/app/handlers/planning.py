"""Planning sessions — the interview engine's tools (TDD #2 §4, §7).

A session is an OBJECT, not a conversation. Notes accumulate into slots across
channels: one thought by SMS at the dock, three more by voice on the drive, the
real work at a keyboard — one session. Nothing depends on a single continuous
conversation, which is the whole point of storing accumulated slot state rather
than a transcript.

`add_planning_note` is the workhorse and must work from SMS in one message.

NOTHING HERE EMITS. `emit_tdd` is a later order, deliberately (§9): building
emission first produces a system that emits with a gate bolted on afterwards,
which is how gates end up bypassable. What exists today can capture, classify,
and be *judged* — and that is the correct intermediate state.

All ungated: capturing a thought is reversible bookkeeping. Diluting the
confirmation gate with reversible work is how a gate stops being read.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import PlanningNote, PlanningSession, Project
from app.planning import ALL_SLOTS, SLOT_QUESTIONS, session_readiness

log = logging.getLogger(__name__)

TARGETS = ("jarvis", "new_project")


def _open_session(db) -> PlanningSession | None:
    return db.execute(
        select(PlanningSession).where(PlanningSession.status == "open")
        .order_by(PlanningSession.id.desc())
    ).scalars().first()


def _touch(s: PlanningSession) -> None:
    s.updated_at = datetime.now(timezone.utc)


def _classify(content: str) -> str | None:
    """Put a captured thought into a slot.

    Haiku, not the conversational model — this is a narrow classifier over
    prose, the same shape as the watch judge and the reflector. **Fails to
    `None`, never to a guess**: an unclassified note is visible in
    `planning_status` and can be re-filed, whereas a confidently wrong slot
    quietly pads someone else's substance and helps a thin session pass the
    gate. Given the gate reads slot content, a bad classification is not a
    cosmetic error — it is a way through.
    """
    from app.llm import create_message

    try:
        resp = create_message(
            system=(
                "You file one note from a design conversation into exactly one slot.\n"
                f"Slots: {', '.join(ALL_SLOTS)}.\n"
                "Answer with the slot name alone, lowercase, nothing else.\n"
                "If the note doesn't clearly belong to one slot, answer: none.\n"
                "Guidance: problem = what's broken; goals = what done looks like; "
                "non_goals = explicitly out of scope; approach = the chosen design; "
                "rejected = an alternative AND why it lost; data_model = schema/state; "
                "tests = what would prove it works; open_questions = what's unresolved."
            ),
            messages=[{"role": "user", "content": f"Note: {content}\n\nSlot?"}],
            tools=[],
            model=settings.jarvis_router_model,
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip().lower()
        word = text.split()[0].strip(".,:") if text else ""
        return word if word in ALL_SLOTS else None
    except Exception as e:  # noqa: BLE001 — classification must never eat a thought
        log.warning("planning note classification failed (filing unclassified): %s", e)
        return None


def _start_planning(args: dict, ctx: Context) -> str:
    topic = (args.get("topic") or "").strip()
    if not topic:
        return "What's the topic? A planning session needs something to be about."

    existing = _open_session(ctx.db)
    if existing:
        # Refused on AMBIGUITY, not tidiness: with two open sessions a stray SMS
        # has no unambiguous home, and filing real thinking under the wrong topic
        # is worse than making the owner close one first.
        return (f"There's already an open planning session on '{existing.topic}' "
                f"(#{existing.id}). Finish or abandon it before starting another — "
                f"with two open, a note sent from a phone has no unambiguous home.")

    target = (args.get("target") or "jarvis").strip().lower()
    if target not in TARGETS:
        return f"Target must be one of: {', '.join(TARGETS)}."

    project_id = None
    ref = (args.get("project") or "").strip()
    if ref:
        p = ctx.db.execute(
            select(Project).where(Project.name.ilike(f"%{ref}%"))
        ).scalars().first()
        if p is None:
            return f"No project matching {ref!r}. Start it without a project, or check the name."
        project_id = p.id

    s = PlanningSession(topic=topic[:500], target=target, project_id=project_id, status="open")
    ctx.db.add(s)
    ctx.db.commit()
    ctx.db.refresh(s)
    return (f"Planning session #{s.id} open: {s.topic}. Target: {target}. "
            f"Send me thoughts as they come — any channel, they all land here. "
            f"First question: {SLOT_QUESTIONS['problem']}")


def _add_planning_note(args: dict, ctx: Context) -> str:
    content = (args.get("content") or "").strip()
    if not content:
        return "Nothing to add."

    s = _open_session(ctx.db)
    if s is None:
        return ("There's no open planning session. Say 'start planning <topic>' and "
                "I'll open one.")

    slot = (args.get("slot") or "").strip().lower() or None
    if slot and slot not in ALL_SLOTS:
        return f"Slot must be one of: {', '.join(ALL_SLOTS)}."
    if slot is None:
        slot = _classify(content)

    n = PlanningNote(session_id=s.id, slot=slot, content=content, channel=ctx.channel)
    ctx.db.add(n)
    _touch(s)
    ctx.db.commit()

    where = f"filed under {slot}" if slot else "unfiled — I couldn't place it"
    r = session_readiness(s.notes)
    if r.ready:
        return f"Noted ({where}). That's every required slot filled."
    nxt = r.missing[0]
    return f"Noted ({where}). Still open: {nxt.slot}. {nxt.question}"


def _planning_status(args: dict, ctx: Context) -> str:
    s = _open_session(ctx.db)
    if s is None:
        return "No open planning session."
    r = session_readiness(s.notes)
    head = (f"Session #{s.id}: {s.topic} (target: {s.target}, "
            f"{len(s.notes)} note(s), filled: {', '.join(r.filled) or 'none'})")
    return f"{head}\n{r.summary()}"


def _next_planning_question(args: dict, ctx: Context) -> str:
    """The single highest-value gap. One question at a time — a planning session
    that feels like a form is one the owner stops using, and an abandoned tool
    captures nothing."""
    s = _open_session(ctx.db)
    if s is None:
        return "No open planning session."
    r = session_readiness(s.notes)
    if r.ready:
        return "Nothing missing — every required slot has real content in it."
    v = r.missing[0]
    return f"{v.question}  (filling: {v.slot})"


def _abandon_planning(args: dict, ctx: Context) -> str:
    reason = (args.get("reason") or "").strip()
    if not reason:
        # Terminal states require a reason for the same reason `parked` does on a
        # project: abandoned-without-one is indistinguishable from forgotten.
        return "Why are we abandoning it? A terminal state without a reason is just a gap."
    s = _open_session(ctx.db)
    if s is None:
        return "No open planning session."
    s.status = "abandoned"
    _touch(s)
    ctx.db.add(PlanningNote(session_id=s.id, slot=None,
                            content=f"[abandoned] {reason}", channel=ctx.channel))
    ctx.db.commit()
    return f"Planning session #{s.id} ('{s.topic}') abandoned: {reason}. The notes are kept."


_SCHEMAS = [
    ({
        "name": "start_planning",
        "description": (
            "Open a planning session on a topic. A session accumulates thoughts across "
            "channels and turns into a design document later. Use when the user wants to "
            "think something through, work out a design, or plan a feature — NOT when they "
            "want a document written now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "What the session is about."},
                "target": {"type": "string", "enum": list(TARGETS),
                           "description": "jarvis (a capability for JARVIS) or new_project."},
                "project": {"type": "string", "description": "Optional tracked project to link."},
            },
            "required": ["topic"],
        },
    }, _start_planning),
    ({
        "name": "add_planning_note",
        "description": (
            "Add one thought to the open planning session. Capture the user's OWN framing "
            "and reasoning, not a summary. If they say why an option is wrong, that belongs "
            "in the 'rejected' slot with the reason. Slot is optional — it's inferred."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The thought, as they said it."},
                "slot": {"type": "string", "enum": list(ALL_SLOTS),
                         "description": "Optional; inferred when omitted."},
            },
            "required": ["content"],
        },
    }, _add_planning_note),
    ({
        "name": "planning_status",
        "description": "Where the open planning session stands: slots filled, what's still "
                       "missing, and the question that would fill the biggest gap.",
        "input_schema": {"type": "object", "properties": {}},
    }, _planning_status),
    ({
        "name": "next_planning_question",
        "description": "The single most useful question to ask next on the open planning "
                       "session. Ask ONE at a time.",
        "input_schema": {"type": "object", "properties": {}},
    }, _next_planning_question),
    ({
        "name": "abandon_planning",
        "description": "Close the open planning session without emitting. Reason required.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    }, _abandon_planning),
]

TOOL_NAMES = [s["name"] for s, _ in _SCHEMAS]


def register(reg: Registry) -> None:
    for schema, fn in _SCHEMAS:
        reg.register(schema, fn)
