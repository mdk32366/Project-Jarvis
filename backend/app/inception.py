"""Inception's date logic — slippage and the timeline (TDD project-inception §4.3–§4.7).

**THE ONE IDEA THIS MODULE MAKES REAL: you cannot slip from a date you never
agreed to.**

A date elicited during an interview is a `proposed` date. It sets no baseline,
and it is invisible to every slippage computation here. Only the owner's
explicit `ratify_plan` writes `baseline_date`, and only then does a milestone
become something that can be late. The difference between "proposed" and
"agreed" is a **stored fact** (`date_status`), not a rendering choice or a tone
— which is the netstatus-stub and fabricated-green lesson in scheduling form: a
commitment JARVIS invented is worse than no commitment, because it looks like a
promise the owner made.

FACT, NEVER JUDGMENT (§6). "Milestone 4: 12 days past baseline" is observable
and true, and the owner draws their own conclusion from it. "You're behind on
this project" is an inference about the owner's work that JARVIS does not
assert. This is the same discipline as exception-first component health, and it
is guarded in test rather than left to prose — the phrases are enumerated below
so a future contributor has to delete an explicit list to break it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Only a RATIFIED milestone can slip. `proposed` and `none` are excluded from
# every computation here — not counted as on-time, not counted as late,
# EXCLUDED. Counting a proposal as on-time would be the same fabrication as
# counting it as late.
RATIFIED = "ratified"
PROPOSED = "proposed"
NO_DATE = "none"

# Words the timeline must never use about the owner's work. Enumerated so the
# guard is a list somebody has to consciously delete, not a habit that erodes.
# "past baseline" is a measurement; "behind" is a verdict on a person.
JUDGMENT_WORDS = ("behind", "late", "overdue", "failing", "slipping badly",
                  "at risk", "poor", "concerning", "should have", "you need to")


def slippage_days(m, *, today: date | None = None) -> int | None:
    """Days past baseline, or None if the milestone cannot slip.

    Returns None — not 0 — for anything unratified or undated. Zero would mean
    "on time", which is a claim; None means "there is nothing to measure",
    which is the truth.

    An OPEN milestone whose date has passed slips against the baseline by
    today's reckoning, not by its planned date: a checkpoint that was due
    three weeks ago and has not been hit is three weeks past, not zero past.
    A DONE milestone is judged on its plan date, since it is no longer running.
    """
    if getattr(m, "date_status", NO_DATE) != RATIFIED:
        return None
    baseline = getattr(m, "baseline_date", None)
    if baseline is None:
        return None

    current = getattr(m, "current_date", None) or baseline
    if getattr(m, "status", "open") == "open":
        current = max(current, today or date.today())

    delta = (current - baseline).days
    return delta if delta > 0 else 0


@dataclass(frozen=True)
class TimelineRow:
    title: str
    date_status: str
    baseline: date | None
    current: date | None
    slipped: int | None
    status: str


def timeline_rows(milestones, *, today: date | None = None) -> list[TimelineRow]:
    return [
        TimelineRow(
            title=m.title,
            date_status=getattr(m, "date_status", NO_DATE),
            baseline=getattr(m, "baseline_date", None),
            current=getattr(m, "current_date", None),
            slipped=slippage_days(m, today=today),
            status=getattr(m, "status", "open"),
        )
        for m in milestones
    ]


def render_timeline(project_name: str, rows: list[TimelineRow],
                    open_risks: list[str], broken_assumptions: list[str]) -> str:
    """"Where is this project" — composed from facts, stated as facts.

    A proposed date is rendered as a PROPOSAL every time it is shown. Rendering
    it like a commitment is exactly the fabrication the stored `date_status`
    exists to prevent — the guard is worthless if the surface flattens it.
    """
    if not rows:
        return f"{project_name}: no milestones recorded yet."

    lines = [f"{project_name} — timeline:"]
    for r in rows:
        if r.date_status == RATIFIED:
            when = f"baseline {r.baseline}"
            if r.current and r.current != r.baseline:
                when += f", now {r.current}"
            if r.slipped:
                when += f" — {r.slipped} days past baseline"
            lines.append(f"  • {r.title} [{r.status}] — {when}")
        elif r.date_status == PROPOSED:
            lines.append(f"  • {r.title} [{r.status}] — {r.current} (PROPOSED, not ratified; "
                         f"no baseline set)")
        else:
            lines.append(f"  • {r.title} [{r.status}] — no date")

    unratified = [r for r in rows if r.date_status != RATIFIED]
    if unratified and not any(r.date_status == RATIFIED for r in rows):
        lines.append("  (Nothing is ratified, so there is no baseline and nothing can slip. "
                     "Ratify the plan to start tracking against it.)")

    if open_risks:
        lines.append(f"  Open risks ({len(open_risks)}):")
        # (rendered below)
        lines += [f"    - {d}" for d in open_risks[:5]]
    if broken_assumptions:
        lines.append(f"  Assumptions now broken ({len(broken_assumptions)}):")
        lines += [f"    - {d}" for d in broken_assumptions[:5]]

    return "\n".join(lines)


# ── Step 7: the brief line (§6) ──────────────────────────────────────────────
def brief_project_lines(db, *, today: date | None = None) -> list[str]:
    """Exception-first project lines for the morning brief.

    **SILENCE IS THE DEFAULT.** A project on or ahead of baseline produces no
    line at all. A project with no ratified baseline is INVISIBLE — there is
    nothing agreed for it to slip from, which is the step-3 fabrication guard
    arriving at the brief layer.

    **FACT, NEVER JUDGMENT.** These lines carry a day count and no verdict, and
    they are checked against the SAME `JUDGMENT_WORDS` list the timeline uses —
    not a second copy. Two lists drift, and the one that drifts is the one
    nobody is looking at.

    An OPEN milestone slips by today's reckoning rather than its plan date, so a
    STALLED project surfaces rather than reporting as on-plan. That is the
    fabricated-green failure in another costume, and it is the case this whole
    line exists to catch.
    """
    from app.config import settings
    from app.models import Milestone, PlanAssumption, PlanRisk, Project

    today = today or date.today()
    floor = settings.project_slippage_brief_days
    out: list[str] = []

    projects = db.query(Project).filter(Project.status == "active").all()
    for p in projects:
        ms = db.query(Milestone).filter(
            Milestone.project_id == p.id, Milestone.status == "open").all()
        slipped = []
        for m in ms:
            d = slippage_days(m, today=today)
            if d is not None and d >= floor:
                slipped.append((m, d))
        if not slipped:
            continue                     # on or ahead of baseline -> no line

        slipped.sort(key=lambda t: -t[1])
        for m, d in slipped[:3]:
            out.append(f"{p.name} — {m.title}: {d} days past baseline "
                       f"(baseline {m.baseline_date}).")
            # A risk that is now biting: linked to a milestone that is slipping.
            bites = db.query(PlanRisk).filter(
                PlanRisk.project_id == p.id, PlanRisk.milestone_id == m.id,
                PlanRisk.status == "open", PlanRisk.plan_status == "live").all()
            for r in bites:
                out.append(f"  Risk flagged at inception, now on a slipping milestone: "
                           f"{r.description}")

    # Broken assumptions — SURFACED ONCE (§4.6). Stamped on surfacing so the
    # brief does not re-alarm every morning; a repeated flag is one the owner
    # learns to skip, and the flag is the whole value.
    fresh = db.query(PlanAssumption).filter(
        PlanAssumption.status == "broken",
        PlanAssumption.surfaced_at.is_(None),
        PlanAssumption.plan_status == "live").all()
    for a in fresh:
        out.append(f"An assumption no longer holds: {a.description}")

    return out


def mark_assumptions_surfaced(db, *, now=None) -> int:
    """Stamp the assumptions `brief_project_lines` just reported.

    Separate from composing them so a brief that FAILS to send does not consume
    the one flag the owner was owed — the stamp belongs with delivery, not with
    rendering.
    """
    from datetime import datetime, timezone

    from app.models import PlanAssumption

    rows = db.query(PlanAssumption).filter(
        PlanAssumption.status == "broken",
        PlanAssumption.surfaced_at.is_(None),
        PlanAssumption.plan_status == "live").all()
    for a in rows:
        a.surfaced_at = now or datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)
