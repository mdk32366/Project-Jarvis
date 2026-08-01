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


def _emit_tdd(args: dict, ctx: Context) -> str:
    """Emit the session as a TDD: branch + PR, never `main` (§7.1).

    **STEP 1 IS THE REFUSAL, and it is the whole reason the gate shipped first.**
    Nothing is composed and no GitHub client is constructed if the session isn't
    ready. The readiness rules are NOT re-implemented here — `session_readiness`
    is called. A second copy of the rules is a second thing that can disagree
    with the first, and the one that gets fixed is whichever happens to be
    failing loudly.
    """
    from app.handlers.repos import _commit_document
    from app.models import ProjectDocument
    from app.planning import compose_document
    from app.secretscan import scan_for_secrets

    s = _open_session(ctx.db)
    if s is None:
        return "No open planning session to emit."

    # ── 1. THE GATE. Refusal is the feature (§5.3). ─────────────────────────
    r = session_readiness(s.notes)
    if not r.ready:
        return ("I'm not writing that up yet — it would be a document with holes in "
                f"it.\n\n{r.summary()}")

    # ── 2. Resolve the destination BEFORE composing, so an unroutable session
    # fails cheaply rather than after building a document nobody can place.
    if s.target == "jarvis":
        project_ref = "JARVIS"
    else:
        if not s.project_id:
            return ("This session targets a new project but isn't linked to one, and I "
                    "won't guess a repo. Link it to a tracked project first.")
        p = ctx.db.get(Project, s.project_id)
        if p is None:
            return "The linked project is gone — relink the session before emitting."
        project_ref = p.name

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = compose_document(s, s.notes, date=date)

    # ── 3. Scan here TOO, and it is not redundant — it is CONTROL FLOW.
    # `commit_document` scans and refuses by returning a human-facing string.
    # Emission has to decide whether to mark the session `emitted`, and deciding
    # that by pattern-matching another tool's prose is a proxy-signal defect
    # waiting to happen (design-note-latch-failures §3). Owning the refusal here
    # means the decision is made on a value, not on wording that may change.
    findings = scan_for_secrets(body)
    if findings:
        where = "; ".join(f"{f.pattern_name} on line {f.line}" for f in findings[:5])
        return (f"I won't publish that — the session notes look like they contain a "
                f"secret: {where}. Nothing was sent to GitHub, and the session is "
                f"still open. Remove the credential from the note and ask again.")

    title = f"TDD — {s.topic}"[:200]

    # ── 4. Write. `commit_document` owns branch+PR, tier->path, its own scan,
    # AND `attach_document` — so emission must NOT attach separately: that
    # would insert a second ProjectDocument and trip project_hygiene's
    # "two live docs of one kind" anomaly. See the PR notes.
    before = {d.id for d in ctx.db.query(ProjectDocument).all()}
    result = _commit_document(
        {"project": project_ref, "title": title, "body": body,
         "tier": "live", "kind": "tdd"}, ctx)

    # ── 5. Success is judged on STATE, not on the returned prose. A new
    # ProjectDocument row is the observable proof the write landed; the message
    # is for the human.
    new = [d for d in ctx.db.query(ProjectDocument).all() if d.id not in before]
    if not new:
        return (f"The write didn't land, so I've left the session open.\n\n{result}")

    doc = new[0]
    s.status = "emitted"
    s.emitted_at = datetime.now(timezone.utc)
    s.document_id = doc.id
    _touch(s)
    ctx.db.commit()

    return (f"Emitted session #{s.id} as a TDD.\n\n{result}\n\n"
            f"It carries the not-build-ready banner and a provenance section "
            f"({len(s.notes)} notes). Bring it to a design session before anyone builds "
            f"from it.")


_EMIT_SCHEMA = {
    "name": "emit_tdd",
    "description": (
        "Write the open planning session up as a TDD and open a pull request. REFUSES "
        "if the session is incomplete, and returns what's missing. Use only when the "
        "user asks to write it up — capturing more thinking is almost always the better "
        "move."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


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


def register_top_level(reg: Registry) -> None:
    """`emit_tdd` — ungated, but TOP-LEVEL ONLY, and the placement is the
    channel control rather than a filing choice.

    **Voice cannot emit (§4.2).** Reviewing a design by having it read aloud is
    not review — there is no scrollback and no "go back to §5" — and emission is
    an outward write that opens a PR on a repo that is now PUBLIC by default
    (§11.3 as ratified). Voice auth is caller-ID and spoofable, so a
    voice-reachable path to it is exactly the blast-radius class the allowlist
    contains. Same reasoning, and the same mechanism, as `commit_document` and
    `create_project_repo`: `orchestrator._run_inner` restricts the top-level
    registry to `VOICE_TOOLS_PHASE1` on a call, so absence from that allowlist
    removes the tool. Fail-closed, not filtered.

    Registering it on the secretary roster instead would have forced it into the
    voice allowlist — every agent is voice-reachable and a roster must be a
    subset of it — which is precisely the trap #58 left a comment about.
    """
    reg.register(_EMIT_SCHEMA, _emit_tdd)
