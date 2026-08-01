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
from app.models import BaselineReset, Milestone, PlanAssumption, PlanRisk, Replan

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

    return render_timeline(p.name, rows, [r.description for r in risks],
                           [a.description for a in broken])


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
]

TOOL_NAMES = [s["name"] for s, _ in _SCHEMAS]


def register(reg: Registry) -> None:
    for schema, fn in _SCHEMAS:
        reg.register(schema, fn)
