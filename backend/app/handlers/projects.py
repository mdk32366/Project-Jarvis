"""Projects & milestones — the durable answer to "where am I on this?"

Every multi-session arc so far has lived in session close-out documents and the
owner's head. Close-outs are excellent narrative records and terrible state
stores: you cannot query them, they go stale the moment work happens, and "what's
left on X" means reading a 200-line document written for a different purpose.
The `ideas` table already commemorates things worth doing; this tracks the things
being *done*.

THE BOUNDARY AGAINST `tasks` (TDD §4.1): a task is a discrete action with a due
date, done in one sitting and then gone. A project is a multi-session arc with
milestones. A milestone is NOT a task — if it wants a due date and a reminder it
is a task, if it wants to be a line in "where am I" it is a milestone. Nothing
enforces this in the schema and nothing should.

NONE OF THESE ARE GATED. Every write here is reversible bookkeeping — no money,
no message to another human, no irreversible external effect. The gate is for
actions you cannot take back, and diluting it with bookkeeping is how a gate
stops being read.

EXCEPTION-FIRST (TDD §6.1): `project_status` reports what's WRONG, not what's
fine. An `active` project with no open milestones and no recent update is either
finished or stalled, and either way the record is lying.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.handlers.base import Context, Registry
from app.models import Idea, Milestone, Project, ProjectDocument

log = logging.getLogger(__name__)

PROJECT_STATUSES = ("active", "parked", "done", "abandoned")
TERMINAL_STATUSES = ("done", "abandoned")
DOC_TIERS = ("live", "archive", "operational")

# An `active` project untouched for this long is reported as an anomaly. Not a
# fault — a bookkeeping smell.
STALE_PROJECT_DAYS = 30

_POSITION_STEP = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; Postgres timestamptz round-trips it. Normalize before
    any arithmetic or `_now() - dt` raises on the naive case."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _touch(p: Project) -> None:
    p.updated_at = _now()


# ── lookup ───────────────────────────────────────────────────────────────────

def _find_project(db, ref: str | int | None) -> tuple[Project | None, str | None]:
    """Resolve a project by id, exact name, or unique partial name.

    Returns `(project, error)`. On ambiguity returns `(None, <disambiguation>)`
    rather than guessing — acting on the wrong project is a silent data error
    that looks like progress.
    """
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        return None, "Which project?"

    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        p = db.get(Project, int(ref))
        return (p, None) if p else (None, f"No project #{ref}.")

    name = str(ref).strip()
    exact = db.execute(select(Project).where(Project.name.ilike(name))).scalars().first()
    if exact:
        return exact, None

    rows = db.execute(
        select(Project).where(Project.name.ilike(f"%{name}%"))
    ).scalars().all()
    if not rows:
        return None, f"No project matching {name!r}."
    if len(rows) > 1:
        names = ", ".join(r.name for r in rows[:6])
        return None, f"{len(rows)} projects match {name!r}: {names}. Which one?"
    return rows[0], None


def _find_milestone(db, project: Project, ref: str | int | None) -> tuple[Milestone | None, str | None]:
    """Resolve a milestone within a project, by id or partial title.

    This is the tool used most, from the worst input device (a phone call). On
    ambiguity it ASKS — completing the wrong milestone is a silent data error
    that looks like progress, which is worse than a clumsy question.
    """
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        return None, "Which milestone?"

    if isinstance(ref, int) or (isinstance(ref, str) and str(ref).strip().isdigit()):
        m = db.get(Milestone, int(ref))
        if m is None or m.project_id != project.id:
            return None, f"No milestone #{ref} on {project.name}."
        return m, None

    text = str(ref).strip()
    rows = [m for m in _milestones(db, project) if text.lower() in m.title.lower()]
    if not rows:
        return None, f"No milestone matching {text!r} on {project.name}."
    if len(rows) > 1:
        titles = "; ".join(f"#{m.id} {m.title}" for m in rows[:6])
        return None, (f"{len(rows)} milestones on {project.name} match {text!r}: {titles}. "
                      "Which one?")
    return rows[0], None


def _milestones(db, project: Project) -> list[Milestone]:
    return db.execute(
        select(Milestone)
        .where(Milestone.project_id == project.id)
        .order_by(Milestone.position, Milestone.id)
    ).scalars().all()


def _progress(db, project: Project) -> tuple[int, int, Milestone | None]:
    """(done, countable, next_open). `dropped` milestones are excluded from BOTH
    numerator and denominator — counting them would overstate progress."""
    rows = _milestones(db, project)
    countable = [m for m in rows if m.status != "dropped"]
    done = [m for m in countable if m.status == "done"]
    nxt = next((m for m in countable if m.status == "open"), None)
    return len(done), len(countable), nxt


# ── anomalies (the exception-first core) ─────────────────────────────────────

def _anomalies(db, project: Project) -> list[str]:
    """Everything WRONG with this project's record. Empty list = nothing to say."""
    out: list[str] = []
    docs = db.execute(
        select(ProjectDocument).where(ProjectDocument.project_id == project.id)
    ).scalars().all()
    live = [d for d in docs if d.tier == "live"]

    if project.status == "active":
        _, total, nxt = _progress(db, project)
        if total == 0:
            out.append("active but has no milestones")
        elif nxt is None:
            out.append("active but every milestone is done or dropped — "
                       "finished, or the record is stale")
        updated = _aware(project.updated_at)
        if updated and (_now() - updated) > timedelta(days=STALE_PROJECT_DAYS):
            days = (_now() - updated).days
            out.append(f"active but untouched for {days} days")

    if not any(d.kind == "tdd" for d in live) and project.status == "active":
        out.append("no live TDD attached")

    by_kind: dict[str, int] = {}
    for d in live:
        by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
    for kind, n in sorted(by_kind.items()):
        if n > 1:
            out.append(f"{n} live '{kind}' documents — there should be one")

    return out


# ── tools ────────────────────────────────────────────────────────────────────

def _create_project(args: dict, ctx: Context) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "A project needs a name."
    if ctx.db.execute(select(Project).where(Project.name.ilike(name))).scalars().first():
        return f"There's already a project called {name!r}."

    status = (args.get("status") or "active").strip().lower()
    if status not in PROJECT_STATUSES:
        return f"Status must be one of: {', '.join(PROJECT_STATUSES)}."

    p = Project(name=name[:200], summary=(args.get("summary") or "")[:5000], status=status)
    ctx.db.add(p)
    ctx.db.commit()
    ctx.db.refresh(p)
    return f"Project #{p.id} created: {p.name}."


def _promote_idea(args: dict, ctx: Context) -> str:
    """Idea -> project. The idea is PRESERVED, not consumed: promotion is a status
    change plus a link, never a move or a delete. The origin of a project is part
    of its history, and it stays commemorated in the ideas repo."""
    ref = args.get("idea_id") or args.get("title") or ""
    idea = None
    if str(ref).strip().isdigit():
        idea = ctx.db.get(Idea, int(ref))
    else:
        text = str(ref).strip()
        if text:
            rows = ctx.db.execute(
                select(Idea).where(Idea.title.ilike(f"%{text}%"))
            ).scalars().all()
            if len(rows) > 1:
                return (f"{len(rows)} ideas match {text!r}: "
                        + "; ".join(f"#{i.id} {i.title}" for i in rows[:6]) + ". Which one?")
            idea = rows[0] if rows else None
    if idea is None:
        return f"No idea matching {ref!r}."

    existing = ctx.db.execute(
        select(Project).where(Project.idea_id == idea.id)
    ).scalars().first()
    if existing:
        return f"Idea #{idea.id} is already project #{existing.id} ({existing.name})."

    name = (args.get("project_name") or idea.title or "").strip()
    if ctx.db.execute(select(Project).where(Project.name.ilike(name))).scalars().first():
        return f"There's already a project called {name!r}. Give it a different name."

    p = Project(
        name=name[:200],
        summary=(args.get("summary") or idea.body or "")[:5000],
        status="active",
        idea_id=idea.id,
    )
    ctx.db.add(p)
    idea.status = "promoted"          # the idea itself stays exactly where it is
    ctx.db.commit()
    ctx.db.refresh(p)
    return (f"Idea #{idea.id} promoted to project #{p.id} ({p.name}). "
            "The idea itself is preserved.")


def _list_projects(args: dict, ctx: Context) -> str:
    status = (args.get("status") or "active").strip().lower()
    q = select(Project)
    if status != "all":
        if status not in PROJECT_STATUSES:
            return f"Status must be 'all' or one of: {', '.join(PROJECT_STATUSES)}."
        q = q.where(Project.status == status)
    rows = ctx.db.execute(q.order_by(Project.updated_at.desc())).scalars().all()
    if not rows:
        return f"No {status} projects." if status != "all" else "No projects yet."

    lines = []
    for p in rows:
        done, total, nxt = _progress(ctx.db, p)
        bit = f"#{p.id} {p.name} [{p.status}]"
        if total:
            bit += f" {done}/{total}"
        if p.status == "parked" and p.parked_reason:
            bit += f" — parked: {p.parked_reason}"
        elif nxt:
            bit += f" — next: {nxt.title}"
        lines.append(bit)
    return f"{len(rows)} project(s):\n" + "\n".join(lines)


def _project_status(args: dict, ctx: Context) -> str:
    """The "where am I" answer. Exception-first: anything wrong comes last and
    loudest, because a record that quietly lies is worse than no record."""
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err

    done, total, nxt = _progress(ctx.db, p)
    out = [f"{p.name} [{p.status}]" + (f" — {p.summary}" if p.summary else "")]
    if p.status == "parked" and p.parked_reason:
        out.append(f"Parked: {p.parked_reason}")
    out.append(f"Milestones: {done}/{total} done" if total else "Milestones: none yet")
    if nxt:
        out.append(f"Next: {nxt.title}" + (f" — {nxt.detail}" if nxt.detail else ""))

    live_tdd = ctx.db.execute(
        select(ProjectDocument).where(
            ProjectDocument.project_id == p.id,
            ProjectDocument.tier == "live",
            ProjectDocument.kind == "tdd",
        )
    ).scalars().first()
    if live_tdd:
        out.append(f"Design: {live_tdd.title}" + (f" ({live_tdd.path})" if live_tdd.path else ""))
    if p.repo_url:
        out.append(f"Repo: {p.repo_url}")

    problems = _anomalies(ctx.db, p)
    if problems:
        out.append("Needs attention: " + "; ".join(problems) + ".")
    return "\n".join(out)


def _add_milestone(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    title = (args.get("title") or "").strip()
    if not title:
        return "A milestone needs a title."

    rows = _milestones(ctx.db, p)
    after_ref = args.get("after")
    if after_ref:
        after, aerr = _find_milestone(ctx.db, p, after_ref)
        if aerr:
            return aerr
        later = [m for m in rows if m.position > after.position]
        # Midpoint between neighbours — sparse positions mean an insertion never
        # renumbers anything else.
        position = ((after.position + later[0].position) // 2 if later
                    else after.position + _POSITION_STEP)
        if later and position in (after.position, later[0].position):
            # Gap exhausted (adjacent integers). Restripe once; rare enough that
            # simplicity beats cleverness.
            for i, m in enumerate(rows, start=1):
                m.position = i * _POSITION_STEP
            ctx.db.flush()
            position = after.position + _POSITION_STEP // 2
    else:
        position = (max((m.position for m in rows), default=0) + _POSITION_STEP)

    m = Milestone(project_id=p.id, title=title[:300],
                  detail=(args.get("detail") or "")[:5000], position=position)
    ctx.db.add(m)
    _touch(p)
    ctx.db.commit()
    ctx.db.refresh(m)
    return f"Milestone #{m.id} added to {p.name}: {m.title}."


def _complete_milestone(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    m, merr = _find_milestone(ctx.db, p, args.get("milestone"))
    if merr:
        return merr

    if m.status == "done":
        # Idempotent: do NOT move completed_at. The moment it was actually
        # finished is the fact worth keeping.
        return f"{m.title} was already done."
    if m.status == "dropped":
        return f"{m.title} was dropped, not open. Re-add it if it's back on."

    m.status = "done"
    m.completed_at = _now()
    _touch(p)
    ctx.db.commit()

    done, total, nxt = _progress(ctx.db, p)
    tail = f" Next: {nxt.title}." if nxt else " That was the last one."
    return f"Done: {m.title}. {p.name} is {done}/{total}.{tail}"


def _drop_milestone(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    m, merr = _find_milestone(ctx.db, p, args.get("milestone"))
    if merr:
        return merr
    reason = (args.get("reason") or "").strip()
    if not reason:
        return "Dropping a milestone needs a reason — it's the difference between "\
               "'no longer relevant' and 'forgotten'."
    if m.status == "dropped":
        return f"{m.title} was already dropped."

    m.status = "dropped"
    m.detail = (f"{m.detail}\n[dropped: {reason}]" if m.detail else f"[dropped: {reason}]")[:5000]
    _touch(p)
    ctx.db.commit()
    return f"Dropped: {m.title} ({reason}). It won't count toward progress."


def _set_project_status(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    status = (args.get("status") or "").strip().lower()
    if status not in PROJECT_STATUSES:
        return f"Status must be one of: {', '.join(PROJECT_STATUSES)}."

    reason = (args.get("reason") or "").strip()
    if status == "parked" and not reason:
        # The one hard rule here. Parked-with-a-reason tells you when to look
        # again; parked-without is indistinguishable from abandoned.
        return ("Parking a project needs a reason — ideally a resumption condition, "
                "like 'until the false-positive rate is known'.")

    p.status = status
    p.parked_reason = reason if status == "parked" else ""
    p.completed_at = _now() if status in TERMINAL_STATUSES else None
    _touch(p)
    ctx.db.commit()
    suffix = f" ({reason})" if reason else ""
    return f"{p.name} is now {status}{suffix}."


def _attach_document(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    title = (args.get("title") or "").strip()
    if not title:
        return "A document needs a title."
    tier = (args.get("tier") or "live").strip().lower()
    if tier not in DOC_TIERS:
        return f"Tier must be one of: {', '.join(DOC_TIERS)}."

    d = ProjectDocument(
        project_id=p.id,
        kind=(args.get("kind") or "other").strip().lower()[:32],
        tier=tier,
        title=title[:300],
        path=(args.get("path") or "")[:400],
        url=(args.get("url") or "")[:400],
    )
    ctx.db.add(d)
    _touch(p)
    ctx.db.commit()
    ctx.db.refresh(d)

    warn = ""
    dupes = [x for x in _anomalies(ctx.db, p) if "live" in x and "documents" in x]
    if dupes:
        warn = " Note: " + "; ".join(dupes) + "."
    return f"Attached '{d.title}' to {p.name} as {d.tier} {d.kind}.{warn}"


def _supersede_document(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    docs = db_docs = ctx.db.execute(
        select(ProjectDocument).where(ProjectDocument.project_id == p.id)
    ).scalars().all()

    def _pick(ref):
        text = str(ref or "").strip()
        if not text:
            return None
        if text.isdigit():
            return next((d for d in docs if d.id == int(text)), None)
        hits = [d for d in db_docs if text.lower() in d.title.lower()]
        return hits[0] if len(hits) == 1 else None

    old = _pick(args.get("document"))
    new = _pick(args.get("superseded_by"))
    if old is None:
        return f"No document matching {args.get('document')!r} on {p.name}."
    if new is None:
        return f"No document matching {args.get('superseded_by')!r} on {p.name}."
    if old.id == new.id:
        return "A document can't supersede itself."

    old.tier = "archive"
    old.superseded_by_id = new.id
    _touch(p)
    ctx.db.commit()
    return f"'{old.title}' archived, superseded by '{new.title}'."


# ── registration ─────────────────────────────────────────────────────────────

_SCHEMAS = [
    ({
        "name": "create_project",
        "description": (
            "Start tracking a multi-session project. Use for an arc of work that spans "
            "sessions and has milestones — NOT for a one-off action with a due date, "
            "which is a task (add_task)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short handle, e.g. 'Location Pull Inversion'"},
                "summary": {"type": "string", "description": "One paragraph: what and why"},
                "status": {"type": "string", "enum": list(PROJECT_STATUSES)},
            },
            "required": ["name"],
        },
    }, _create_project),

    ({
        "name": "promote_idea",
        "description": (
            "Turn a captured idea into a tracked project. The idea is preserved, not "
            "consumed — it stays in the ideas list, linked to the new project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "idea_id": {"type": "integer"},
                "title": {"type": "string", "description": "Partial idea title, if the id isn't known"},
                "project_name": {"type": "string", "description": "Defaults to the idea's title"},
                "summary": {"type": "string"},
            },
        },
    }, _promote_idea),

    ({
        "name": "list_projects",
        "description": (
            "List tracked projects with milestone progress. Defaults to active ones — "
            "pass status='all' for everything, or parked/done/abandoned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "active (default), parked, done, abandoned, or all"},
            },
        },
    }, _list_projects),

    ({
        "name": "project_status",
        "description": (
            "Where things stand on one project: status, milestone progress, the next open "
            "milestone, its live design doc, and anything wrong with the record. This is "
            "the 'where am I on X' answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "Name or id"}},
            "required": ["project"],
        },
    }, _project_status),

    ({
        "name": "add_milestone",
        "description": (
            "Add a checkpoint to a project. A milestone is a step in the arc, not a "
            "dated to-do. Use 'after' to insert it between existing milestones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "after": {"type": "string", "description": "Title or id of the milestone it follows"},
            },
            "required": ["project", "title"],
        },
    }, _add_milestone),

    ({
        "name": "complete_milestone",
        "description": (
            "Mark a milestone done. Accepts a partial title; if more than one matches it "
            "will ask rather than guess."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "milestone": {"type": "string", "description": "Title (partial is fine) or id"},
            },
            "required": ["project", "milestone"],
        },
    }, _complete_milestone),

    ({
        "name": "drop_milestone",
        "description": (
            "Mark a milestone as no longer relevant. Requires a reason. A dropped "
            "milestone does not count toward progress — it was not achieved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "milestone": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["project", "milestone", "reason"],
        },
    }, _drop_milestone),

    ({
        "name": "set_project_status",
        "description": (
            "Change a project's status: active, parked, done, or abandoned. Parking "
            "REQUIRES a reason — ideally a resumption condition."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "status": {"type": "string", "enum": list(PROJECT_STATUSES)},
                "reason": {"type": "string", "description": "Required when parking"},
            },
            "required": ["project", "status"],
        },
    }, _set_project_status),

    ({
        "name": "attach_document",
        "description": (
            "Record a document belonging to a project. Tier is live (current design), "
            "archive (superseded), or operational (executed handoff/checklist)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "kind": {"type": "string", "description": "tdd, test-plan, ui-plan, closeout, readme, other"},
                "tier": {"type": "string", "enum": list(DOC_TIERS)},
                "title": {"type": "string"},
                "path": {"type": "string", "description": "Repo-relative, e.g. docs/TDD-foo.md"},
                "url": {"type": "string"},
            },
            "required": ["project", "title"],
        },
    }, _attach_document),

    ({
        "name": "supersede_document",
        "description": (
            "Mark one project document as replaced by another. The old one moves to the "
            "archive tier and records what replaced it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "document": {"type": "string", "description": "Title or id of the doc being replaced"},
                "superseded_by": {"type": "string", "description": "Title or id of the replacement"},
            },
            "required": ["project", "document", "superseded_by"],
        },
    }, _supersede_document),
]

TOOL_NAMES = [schema["name"] for schema, _ in _SCHEMAS]


def register(reg: Registry) -> None:
    for schema, fn in _SCHEMAS:
        reg.register(schema, fn)
