"""Inception's date tools — propose, ratify, replan, re-baseline, read the timeline.

Steps 3–4 of the inception TDD. This is where step 1's columns get behaviour.

**THE GOVERNING INVARIANT: only `_ratify_plan` and `_reset_baseline` may write
`baseline_date`.** Proposing a date does not. Replanning does not. Seeding does
not. That is what makes "you cannot slip from a date you never agreed to" a
property of the code rather than an intention in a document — and a test greps
this module to keep it that way, because the tempting shortcut (set the baseline
when the date is first proposed, it's simpler) destroys the whole guarantee
while looking like a tidy-up.

A REPLAN IS A LOGGED EVENT, NOT A FIELD EDIT (§4.4). The row is written FIRST,
then the date moves. "Why did milestone 4 slip three weeks" is answerable only
because the move was captured as a thing that happened. A system that overwrote
the cell can tell you where the date is now and nothing about how it got there.

All ungated: these are reversible bookkeeping on JARVIS's own records — no
money, no outward message, nothing another human sees. The confirmation gate is
for what you cannot take back, and diluting it is how a gate stops being read.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.handlers.base import Context, Registry
from app.handlers.projects import _find_milestone, _find_project, _milestones, _touch
from app.inception import (NO_DATE, PROPOSED, RATIFIED, render_timeline,
                           slippage_days, timeline_rows)
from app.models import (BaselineReset, Milestone, PlanAssumption, PlanRisk,
                        Project, Replan)

log = logging.getLogger(__name__)


def _parse_date(raw: str | None) -> tuple[date | None, str | None]:
    """(date, error). Strict ISO — a date guessed from ambiguous input is the
    same class of fabrication this whole module exists to prevent."""
    text = (raw or "").strip()
    if not text:
        return None, "I need a date, as YYYY-MM-DD."
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"I couldn't read {text!r} as a date — use YYYY-MM-DD."


def _propose_milestone_date(args: dict, ctx: Context) -> str:
    """Record a date the owner floated, or JARVIS elicited, as a PROPOSAL.

    Sets `current_date` and `date_status='proposed'`. **Writes no baseline** —
    that is the fabrication guard, and it is the reason this tool exists
    separately from ratification rather than doing both.
    """
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    m, merr = _find_milestone(ctx.db, p, args.get("milestone"))
    if merr:
        return merr

    when, derr = _parse_date(args.get("date"))
    if derr:
        return derr

    if m.date_status == RATIFIED:
        return (f"'{m.title}' is already ratified with a baseline of {m.baseline_date}. "
                f"Moving it now is a replan — say why it's moving and I'll log it.")

    m.current_date = when
    m.date_status = PROPOSED
    _touch(p)
    ctx.db.commit()
    return (f"Noted {when} for '{m.title}' as a PROPOSAL. No baseline is set and nothing "
            f"can slip from it yet — ratify the plan when the dates are agreed.")


def _ratify_plan(args: dict, ctx: Context) -> str:
    """THE ONE-WAY GATE INTO TRACKING (§4.3). The only routine path that writes
    a baseline.

    For each proposed milestone: `baseline_date = current_date`, once, and
    `date_status='ratified'`. After this the baseline never moves except through
    an explicit, logged `reset_baseline`.
    """
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err

    ms = _milestones(ctx.db, p)
    proposed = [m for m in ms if m.date_status == PROPOSED and m.current_date]
    if not proposed:
        already = [m for m in ms if m.date_status == RATIFIED]
        if already:
            return (f"{p.name} is already ratified ({len(already)} milestone(s) with "
                    f"baselines). Use replan to move a date, or reset_baseline if the plan "
                    f"itself changed.")
        return (f"{p.name} has no proposed dates to ratify. Propose dates on its milestones "
                f"first — I won't invent them.")

    undated = [m for m in ms if m.date_status == NO_DATE]
    for m in proposed:
        m.baseline_date = m.current_date      # set ONCE, here and nowhere else
        m.date_status = RATIFIED
    _touch(p)
    ctx.db.commit()

    note = ""
    if undated:
        note = (f" {len(undated)} milestone(s) still have no date and stay untracked — "
                f"they can't slip from nothing.")
    return (f"Ratified {len(proposed)} milestone(s) on {p.name}. Their baselines are set and "
            f"slippage is now measured against them.{note}")


def _replan(args: dict, ctx: Context) -> str:
    """Move a milestone's date — as a LOGGED EVENT (§4.4).

    Row first, then the date. `baseline_date` is untouched: a replan moves the
    plan, not the thing the plan is measured against.
    """
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    m, merr = _find_milestone(ctx.db, p, args.get("milestone"))
    if merr:
        return merr

    reason = (args.get("reason") or "").strip()
    if not reason:
        # Refused at the tool as well as NOT NULL at the column. The column stops
        # a direct write; this stops the tool from being the thing that makes an
        # unexplained move easy.
        return ("Why is it moving? A replan without a reason is just an edit, and the "
                "whole point of logging it is being able to answer that later.")

    when, derr = _parse_date(args.get("new_date"))
    if derr:
        return derr

    previous = m.current_date

    # THE ORDER IS THE POINT. The event is committed before the date moves, so a
    # failure between the two leaves a recorded intent and an unmoved date —
    # never a moved date nobody can explain.
    ctx.db.add(Replan(milestone_id=m.id, from_date=previous, to_date=when, reason=reason))
    ctx.db.flush()

    m.current_date = when
    if m.date_status == NO_DATE:
        m.date_status = PROPOSED     # a replanned undated milestone is still not agreed
    _touch(p)
    ctx.db.commit()

    slip = slippage_days(m)
    tail = ""
    if slip:
        tail = f" That puts it {slip} days past its baseline of {m.baseline_date}."
    elif m.date_status != RATIFIED:
        tail = " It has no baseline, so there's nothing for it to slip against."
    return f"Moved '{m.title}' from {previous or 'no date'} to {when}: {reason}.{tail}"


def _reset_baseline(args: dict, ctx: Context) -> str:
    """Re-baseline a project — the rare legitimate case where the plan genuinely
    changed rather than merely slipped (§4.7).

    **Snapshots the entire prior baseline before overwriting.** A re-baseline
    that isn't logged is indistinguishable from hiding a slip, so the snapshot
    is the guard and it happens first.
    """
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err

    reason = (args.get("reason") or "").strip()
    if not reason:
        return ("Re-baselining needs a reason. Moving the line you're measured against "
                "without saying why is indistinguishable from hiding a slip.")

    ms = [m for m in _milestones(ctx.db, p) if m.date_status == RATIFIED]
    if not ms:
        return f"{p.name} has no ratified baseline to reset."

    snapshot = json.dumps([
        {"milestone_id": m.id, "title": m.title,
         "baseline_date": m.baseline_date.isoformat() if m.baseline_date else None,
         "current_date": m.current_date.isoformat() if m.current_date else None}
        for m in ms
    ])
    ctx.db.add(BaselineReset(project_id=p.id, snapshot=snapshot, reason=reason))
    ctx.db.flush()          # the snapshot lands BEFORE anything is overwritten

    moved = 0
    for m in ms:
        if m.current_date and m.current_date != m.baseline_date:
            m.baseline_date = m.current_date
            moved += 1
    _touch(p)
    ctx.db.commit()

    return (f"Re-baselined {p.name}: {moved} milestone(s) now measured from their current "
            f"dates. The previous baseline is snapshotted against the reason you gave, so "
            f"the old line is still recoverable.")


def _project_timeline(args: dict, ctx: Context) -> str:
    """Read-only. Facts and day counts; never a verdict (§4.7, §6)."""
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err

    rows = timeline_rows(_milestones(ctx.db, p), today=datetime.now(timezone.utc).date())
    risks = ctx.db.execute(
        select(PlanRisk).where(PlanRisk.project_id == p.id, PlanRisk.status == "open")
    ).scalars().all()
    broken = ctx.db.execute(
        select(PlanAssumption).where(PlanAssumption.project_id == p.id,
                                     PlanAssumption.status == "broken")
    ).scalars().all()

    out = render_timeline(p.name, rows, [r.description for r in risks],
                          [a.description for a in broken])

    # ORPHANED DRAFTS ARE VISIBLE, NEVER SILENT (§11.8 applied to the two-system
    # write). A draft row means an emit failed part-way; if it rendered as a
    # normal row it would be a partial masquerading as done.
    orphans = ctx.db.execute(
        select(PlanRisk).where(PlanRisk.project_id == p.id,
                               PlanRisk.plan_status == "draft")).scalars().all()
    orphans += ctx.db.execute(
        select(PlanAssumption).where(PlanAssumption.project_id == p.id,
                                     PlanAssumption.plan_status == "draft")).scalars().all()
    if orphans:
        out += (f"\n  ⚠ {len(orphans)} draft row(s) left over from an emit that did not "
                f"complete — the plan document did not land. Re-run the emit or clear them.")
    return out


# ── Step 6: emit — the atomicity resolution (§11) ───────────────────────────
def _emit_project_plan(args: dict, ctx: Context) -> str:
    """Turn a `project_plan` session into a committed plan document AND seeded
    live rows — or into nothing at all.

    THE ATOMICITY RESOLUTION (§11). A GitHub PR and a DB transaction cannot
    share one transaction, so the sequence is chosen so the *reversible* half
    goes first:

        seed rows as `draft`  ->  commit the doc  ->  promote to `live`
                                       |
                                       +-- on failure: delete the drafts

    Either everything landed (rows live, doc committed) or nothing did (drafts
    gone, session still open). The one state that must never exist silently is a
    half-landed one — and if a draft somehow outlives a failure, it is VISIBLE
    in `project_timeline` rather than masquerading as real. That is §11.8's
    partial-reported-as-partial lesson applied to a two-system write.

    REFUSES BY CALLING #2's GATE, extended to the project slot set in #61. No
    new readiness logic — a second copy of the rules is a second thing that can
    disagree with the first.
    """
    from app.handlers.planning import _open_session
    from app.handlers.repos import _commit_document
    from app.models import ProjectDocument
    from app.planning import compose_slots, session_readiness
    from app.secretscan import scan_for_secrets

    s = _open_session(ctx.db)
    if s is None:
        return "No open planning session to emit."
    if s.target != "project_plan":
        return (f"Session #{s.id} is a '{s.target}' session — use emit_tdd for that. "
                f"emit_project_plan is for inception sessions.")

    # ── 1. THE GATE. Nothing is seeded and no client is built if it refuses.
    r = session_readiness(s.notes, target=s.target)
    if not r.ready:
        return ("I'm not writing that up yet — it would be a plan with holes in it.\n\n"
                f"{r.summary()}")

    if not s.project_id:
        return ("This session isn't linked to a project, and I won't guess one. "
                "Link it to a tracked project, then emit.")
    p = ctx.db.get(Project, s.project_id)
    if p is None:
        return "The linked project is gone — relink the session before emitting."

    # REPO CREATION STAYS BEHIND ITS OWN GATE. `create_project_repo` is a GATED
    # tool: the confirmation gate runs in `orchestrator.run`, so calling it from
    # inside this ungated tool would execute an irreversible outward action with
    # no confirmation — a gate bypass wearing a convenience. Emission refuses and
    # points at the gated tool instead. (The order's §6.3 says emit "calls"
    # create_project_repo; it cannot, and this is why.)
    if not p.repo_url:
        return (f"{p.name} has no repo yet, and creating one is a gated action I can't "
                f"take from inside an emit. Say 'create the repo for {p.name}' first — "
                f"I'll confirm it with you — then emit the plan.")

    slots = compose_slots(s.notes)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = _compose_plan_document(s, p, slots, date_str)

    # Scan here as control flow, not belt-and-braces: `commit_document` refuses
    # by returning prose, and this tool has to decide whether to promote rows.
    # Making that decision by pattern-matching another tool's wording is the
    # proxy-signal defect one layer up.
    findings = scan_for_secrets(body)
    if findings:
        where = "; ".join(f"{f.pattern_name} on line {f.line}" for f in findings[:5])
        return (f"I won't publish that — the session notes look like they contain a "
                f"secret: {where}. Nothing was seeded and nothing was sent to GitHub.")

    # ── 2. SEED AS DRAFT. Reversible half first.
    drafts = _seed_draft_rows(ctx.db, p, s.notes)
    ctx.db.commit()

    # ── 3. COMMIT THE DOCUMENT (real `commit_document`, #54 — never stubbed:
    # TDD #3 shipped, so the TDD's "stub until #3 exists" note is dead).
    before = {d.id for d in ctx.db.query(ProjectDocument).all()}
    try:
        result = _commit_document(
            {"project": p.name, "title": f"Plan — {s.topic}"[:200], "body": body,
             "tier": "live", "kind": "tdd"}, ctx)
    except Exception as e:  # noqa: BLE001 — a failed write must roll the drafts back
        _delete_drafts(ctx.db, drafts)
        ctx.db.commit()
        return (f"The plan document didn't commit, so I've removed the draft rows and left "
                f"the session open — nothing half-landed. ({e})")

    new_docs = [d for d in ctx.db.query(ProjectDocument).all() if d.id not in before]
    if not new_docs:
        # Success is judged on STATE, not on the returned prose.
        _delete_drafts(ctx.db, drafts)
        ctx.db.commit()
        return (f"The plan document didn't commit, so I've removed the draft rows and left "
                f"the session open — nothing half-landed.\n\n{result}")

    # ── 4. PROMOTE. Only now do the rows become real.
    for row in drafts:
        row.plan_status = "live"
    s.status = "emitted"
    s.emitted_at = datetime.now(timezone.utc)
    s.document_id = new_docs[0].id
    _touch(p)
    ctx.db.commit()

    return (f"Emitted the plan for {p.name}: {len(drafts)} risk/assumption row(s) seeded "
            f"and the document committed.\n\n{result}\n\nThe rows and the document landed "
            f"together — ratify the milestone dates when you're ready to track against them.")


def _seed_draft_rows(db, project, notes) -> list:
    """One captured note -> one row, VERBATIM.

    Notes are already the atomic unit of capture and are never rewritten, so a
    risk row is a risk the owner actually stated rather than a summary of
    several. No LLM extraction: inventing structure from prose is exactly the
    fabrication this arc spent seven steps refusing.

    Milestones are deliberately NOT seeded from prose. A milestone needs a title
    and a date, and manufacturing either from a paragraph would fabricate a
    commitment — `add_milestone` + `propose_milestone_date` exist for that, under
    the owner's hand.
    """
    made = []
    for n in sorted(notes, key=lambda x: (x.id or 0)):
        text = (n.content or "").strip()
        if not text:
            continue
        if n.slot == "risks":
            row = PlanRisk(project_id=project.id, description=text, plan_status="draft")
        elif n.slot == "assumptions":
            row = PlanAssumption(project_id=project.id, description=text, plan_status="draft")
        else:
            continue
        db.add(row)
        made.append(row)
    db.flush()
    return made


def _delete_drafts(db, rows) -> None:
    for row in rows:
        try:
            db.delete(row)
        except Exception:  # noqa: BLE001 — rollback must not itself explode
            log.warning("could not remove draft row %r", getattr(row, "id", "?"))


def _compose_plan_document(session, project, slots: dict, date_str: str) -> str:
    """The plan document. Carries TDD #2's not-build-ready banner and a
    provenance section, for the same reasons: a document that LOOKS build-ready
    and isn't is worse than none, and provenance makes thin content visible even
    when it passes the gate."""
    from app.planning import BANNER

    channels = sorted({n.channel for n in session.notes if n.channel})
    parts = [
        BANNER.format(date=date_str), "",
        f"# Plan — {session.topic}", "",
        f"_Project: {project.name}_", "",
    ]
    for slot, heading in (
        ("problem", "1. Problem"), ("objectives", "2. Objectives"),
        ("goals", "3. Goals"), ("non_goals", "4. Non-goals"),
        ("approach", "5. Approach"), ("milestones", "6. Milestones"),
        ("risks", "7. Risks"), ("assumptions", "8. Assumptions"),
        ("tasks", "9. Near-term tasks"), ("rejected", "10. Alternatives rejected"),
        ("open_questions", "11. Open questions"),
    ):
        parts += [f"## {heading}", "", slots.get(slot, "").strip() or "_(not recorded)_", ""]

    parts += [
        "---", "", "## Provenance", "",
        f"- Planning session: #{session.id}",
        f"- Opened: {session.created_at}",
        f"- Emitted: {date_str}",
        f"- Channels used: {', '.join(channels) or 'none recorded'}",
        f"- Notes captured: {len(session.notes)}", "",
        "_Milestone dates in this plan are PROPOSALS until ratified. No baseline "
        "exists and nothing can slip until `ratify_plan` is run._", "",
    ]
    return "\n".join(parts)


_EMIT_SCHEMA = {
    "name": "emit_project_plan",
    "description": (
        "Write an open project_plan (inception) session up as a plan document and seed its "
        "risks and assumptions as live rows. REFUSES if the session is incomplete. Opens a "
        "pull request; never commits to main."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


# ── Step 5: risks and assumptions as living rows (§4.6) ─────────────────────
def _flag_risk(args: dict, ctx: Context) -> str:
    """Name a risk. A risk buried in a document is inert; a risk as a ROW can be
    resurfaced when it bites."""
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    desc = (args.get("description") or "").strip()
    if not desc:
        return "What's the risk? I won't file an empty one."

    milestone_id = None
    if (args.get("milestone") or "").strip():
        m, merr = _find_milestone(ctx.db, p, args.get("milestone"))
        if merr:
            return merr
        milestone_id = m.id

    ctx.db.add(PlanRisk(project_id=p.id, milestone_id=milestone_id, description=desc))
    _touch(p)
    ctx.db.commit()
    link = " linked to that milestone" if milestone_id else ""
    return f"Risk logged on {p.name}{link}: {desc}"


def _break_assumption(args: dict, ctx: Context) -> str:
    """Flip an assumption to `broken`. The brief surfaces it ONCE."""
    a = ctx.db.get(PlanAssumption, int(args.get("assumption_id") or 0))
    if a is None:
        return f"No assumption #{args.get('assumption_id')}."
    if a.status == "broken":
        return f"Assumption #{a.id} is already recorded as broken: {a.description}"
    a.status = "broken"
    a.surfaced_at = None          # not yet reported — the brief will say it once
    ctx.db.commit()
    return (f"Recorded as broken: {a.description}. It'll surface in the brief once, "
            f"then stop — a broken assumption is worth one flag, not a daily one.")


def _resolve_risk(args: dict, ctx: Context) -> str:
    """Close a risk as `realized` (it fired) or `retired` (no longer live).

    Kept apart deliberately: a risk that stopped applying is a different outcome
    from one that came true, and collapsing them would understate what actually
    went wrong on a project.
    """
    r = ctx.db.get(PlanRisk, int(args.get("risk_id") or 0))
    if r is None:
        return f"No risk #{args.get('risk_id')}."
    outcome = (args.get("outcome") or "").strip().lower()
    if outcome not in ("realized", "retired"):
        return "Outcome must be 'realized' (it happened) or 'retired' (no longer a risk)."
    r.status = outcome
    ctx.db.commit()
    word = "came true" if outcome == "realized" else "no longer applies"
    return f"Risk #{r.id} marked {outcome} — {word}: {r.description}"


def _list_plan_risks(args: dict, ctx: Context) -> str:
    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err
    rows = ctx.db.execute(
        select(PlanRisk).where(PlanRisk.project_id == p.id).order_by(PlanRisk.id)
    ).scalars().all()
    asums = ctx.db.execute(
        select(PlanAssumption).where(PlanAssumption.project_id == p.id)
        .order_by(PlanAssumption.id)
    ).scalars().all()
    if not rows and not asums:
        return f"{p.name} has no recorded risks or assumptions."

    lines = []
    if rows:
        lines.append("Risks:")
        lines += [f"  #{r.id} [{r.status}] {r.description}" for r in rows]
    if asums:
        lines.append("Assumptions:")
        lines += [f"  #{a.id} [{a.status}] {a.description}" for a in asums]
    return "\n".join(lines)


_SCHEMAS = [
    ({
        "name": "propose_milestone_date",
        "description": (
            "Record a PROPOSED date for a milestone — a date floated or elicited, not yet "
            "agreed. Sets no baseline and nothing can slip from it. Use while planning; "
            "ratify_plan is what turns proposals into commitments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "milestone": {"type": "string", "description": "Milestone title or id."},
                "date": {"type": "string", "description": "YYYY-MM-DD."},
            },
            "required": ["project", "milestone", "date"],
        },
    }, _propose_milestone_date),
    ({
        "name": "ratify_plan",
        "description": (
            "Accept a project's proposed milestone dates as agreed. Sets each baseline "
            "equal to its current date — the ONLY routine way a baseline is written. "
            "Only do this when the user explicitly agrees to the dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    }, _ratify_plan),
    ({
        "name": "replan",
        "description": (
            "Move a milestone's date, logging why. The baseline does NOT move — this "
            "records a slip against it. A reason is required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "milestone": {"type": "string"},
                "new_date": {"type": "string", "description": "YYYY-MM-DD."},
                "reason": {"type": "string", "description": "Why it's moving. Required."},
            },
            "required": ["project", "milestone", "new_date", "reason"],
        },
    }, _replan),
    ({
        "name": "reset_baseline",
        "description": (
            "Re-baseline a project when the PLAN genuinely changed (not merely slipped). "
            "Snapshots the old baseline first. Reason required. Rare — prefer replan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["project", "reason"],
        },
    }, _reset_baseline),
    ({
        "name": "project_timeline",
        "description": (
            "Where a project stands against its plan: each milestone's baseline and current "
            "date, days past baseline where there are any, open risks, and broken "
            "assumptions. Reports facts, not a verdict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    }, _project_timeline),
    ({
        "name": "flag_risk",
        "description": (
            "Record a risk to a project's plan — something that could go wrong. Optionally "
            "link it to the milestone it threatens, so it can be resurfaced when that "
            "milestone is active or slipping."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "description": {"type": "string", "description": "The risk, in their words."},
                "milestone": {"type": "string", "description": "Optional milestone it threatens."},
            },
            "required": ["project", "description"],
        },
    }, _flag_risk),
    ({
        "name": "break_assumption",
        "description": (
            "Record that a stated assumption turned out to be false. It surfaces in the "
            "brief once, then stops."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"assumption_id": {"type": "integer"}},
            "required": ["assumption_id"],
        },
    }, _break_assumption),
    ({
        "name": "resolve_risk",
        "description": (
            "Close a risk: 'realized' if it actually happened, 'retired' if it no longer "
            "applies. These are different outcomes and are kept apart."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_id": {"type": "integer"},
                "outcome": {"type": "string", "enum": ["realized", "retired"]},
            },
            "required": ["risk_id", "outcome"],
        },
    }, _resolve_risk),
    ({
        "name": "list_plan_risks",
        "description": "List a project's recorded risks and assumptions with their ids and status.",
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    }, _list_plan_risks),
]

TOOL_NAMES = [s["name"] for s, _ in _SCHEMAS]


def register(reg: Registry) -> None:
    for schema, fn in _SCHEMAS:
        reg.register(schema, fn)


def register_top_level(reg: Registry) -> None:
    """`emit_project_plan` — ungated, TOP-LEVEL ONLY, and the placement is the
    channel control.

    Emission is an OUTWARD WRITE: a plan document committed to a repo that is
    public by default under the ratified visibility rule (§11.3). Voice auth is
    caller-ID and spoofable, so a voice path to it is the blast-radius class the
    allowlist exists to contain — the same reasoning, and the same mechanism, as
    `emit_tdd` (#59) and `commit_document` (#54).

    Capture, ratify and replan ARE voice-reachable and that is deliberate: they
    are logged and recoverable. Emit is the one that leaves the building.
    """
    reg.register(_EMIT_SCHEMA, _emit_project_plan)
