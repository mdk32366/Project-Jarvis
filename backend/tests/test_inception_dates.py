"""Inception steps 3-4: ratification, the baseline, replan, slippage, timeline.

The two load-bearing tests here:

  * `test_only_ratify_plan_writes_a_baseline` — the fabrication guard as a
    property of the code. The tempting shortcut (set the baseline when the date
    is first proposed; it's simpler) destroys the whole guarantee while looking
    like a tidy-up, so it is greppped for as well as exercised.
  * `test_the_timeline_states_facts_and_never_a_verdict` — §6. "12 days past
    baseline" is observable; "you're behind" is an inference about the owner's
    work that JARVIS does not make.
"""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.handlers.base import Context, build_registry
from app.handlers.inception import (_project_timeline, _propose_milestone_date,
                                    _ratify_plan, _replan, _reset_baseline)
from app.inception import JUDGMENT_WORDS, slippage_days
from app.models import (BaselineReset, Milestone, PlanAssumption, PlanRisk, Project,
                        Replan, Task)

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture
def project(db):
    p = Project(name="Boat restoration", summary="the arc", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    for i, title in enumerate(("Hull sealed", "Engine rebuilt", "Sea trial"), start=1):
        db.add(Milestone(project_id=p.id, title=title, position=i * 10))
    db.commit()
    db.refresh(p)
    return p


def _m(db, title: str) -> Milestone:
    return db.query(Milestone).filter_by(title=title).one()


# ── STEP 3: proposed vs ratified ─────────────────────────────────────────────
def test_a_proposed_date_sets_no_baseline(db, ctx, project):
    """THE FABRICATION GUARD. An elicited date is a suggestion, not an
    agreement — so it cannot become something to be late against."""
    msg = _propose_milestone_date(
        {"project": "Boat restoration", "milestone": "Hull sealed", "date": "2026-09-01"}, ctx)

    m = _m(db, "Hull sealed")
    assert m.current_date == date(2026, 9, 1)
    assert m.date_status == "proposed"
    assert m.baseline_date is None, "a proposal must never write a baseline"
    assert "PROPOSAL" in msg and "nothing can slip" in msg


def test_ratify_sets_baseline_equal_to_current_once(db, ctx, project):
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2026-09-01"}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)

    m = _m(db, "Hull sealed")
    assert m.date_status == "ratified"
    assert m.baseline_date == m.current_date == date(2026, 9, 1)


def test_ratify_refuses_to_invent_dates(db, ctx, project):
    """Nothing proposed → nothing to ratify. JARVIS does not fill the gap."""
    msg = _ratify_plan({"project": "Boat restoration"}, ctx)
    assert "no proposed dates" in msg and "won't invent" in msg
    assert all(m.baseline_date is None for m in db.query(Milestone).all())


def test_only_ratify_plan_writes_a_baseline(db, ctx, project):
    """THE INVARIANT, checked two ways.

    Behaviourally: proposing and replanning leave `baseline_date` NULL.
    Structurally: `baseline_date` is assigned in exactly two functions —
    `_ratify_plan` and `_reset_baseline` — because the tempting shortcut (set it
    when the date is first proposed) looks like a simplification and silently
    destroys the guarantee.
    """
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2026-09-01"}, ctx)
    _replan({"project": "Boat restoration", "milestone": "Hull sealed",
             "new_date": "2026-10-01", "reason": "the yard lost the slot"}, ctx)
    assert _m(db, "Hull sealed").baseline_date is None, "a replan wrote a baseline"

    src = (BACKEND / "app" / "handlers" / "inception.py").read_text(encoding="utf-8")
    owners = []
    current = None
    for line in src.splitlines():
        fn = re.match(r"def (_\w+)\(", line)
        if fn:
            current = fn.group(1)
        if re.search(r"\.baseline_date\s*=(?!=)", line):
            owners.append(current)
    assert set(owners) == {"_ratify_plan", "_reset_baseline"}, \
        f"baseline_date is written in unexpected places: {sorted(set(owners))}"


def test_an_unratified_project_reports_no_slippage(db, ctx, project):
    """It cannot slip, because there is nothing agreed to slip from."""
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2020-01-01"}, ctx)          # long past

    m = _m(db, "Hull sealed")
    assert slippage_days(m) is None, "a proposal must not be measured"

    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "PROPOSED" in out
    assert "past baseline" not in out
    assert "nothing can slip" in out


def test_slippage_is_none_not_zero_for_undated(db, project):
    """None means 'nothing to measure'. Zero would be a claim of on-time."""
    assert slippage_days(_m(db, "Sea trial")) is None


def test_a_milestone_creates_no_reminder(db, ctx, project):
    """§4.5, asserted: a milestone date is a plan the timeline reads; it does not
    ping. Collapsing the two means either every milestone nags or tasks stop
    reminding."""
    before = db.query(Task).count()
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2026-09-01"}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)
    assert db.query(Task).count() == before, "a milestone created a reminder"

    src = (BACKEND / "app" / "handlers" / "inception.py").read_text(encoding="utf-8")
    assert "Task(" not in src and "add_task" not in src


# ── STEP 4: replan, slippage, timeline ───────────────────────────────────────
def _ratified(db, ctx, title="Hull sealed", when="2026-09-01") -> Milestone:
    _propose_milestone_date({"project": "Boat restoration", "milestone": title,
                             "date": when}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)
    return _m(db, title)


def test_replan_writes_the_event_and_leaves_the_baseline_alone(db, ctx, project):
    _ratified(db, ctx)
    _replan({"project": "Boat restoration", "milestone": "Hull sealed",
             "new_date": "2026-09-13", "reason": "the yard lost the slot"}, ctx)

    r = db.query(Replan).one()
    assert r.from_date == date(2026, 9, 1) and r.to_date == date(2026, 9, 13)
    assert "yard" in r.reason

    m = _m(db, "Hull sealed")
    assert m.current_date == date(2026, 9, 13)
    assert m.baseline_date == date(2026, 9, 1), "a replan moved the baseline"


def test_the_event_is_written_before_the_date_moves(db, ctx, project, monkeypatch):
    """Order is the point (§4.4). If the log write fails, the date must NOT have
    moved — a moved date nobody can explain is the overwrite-the-cell
    anti-pattern this design rejects."""
    _ratified(db, ctx)
    original = _m(db, "Hull sealed").current_date

    real_flush = db.flush

    def _boom(*a, **k):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(db, "flush", _boom)
    with pytest.raises(RuntimeError):
        _replan({"project": "Boat restoration", "milestone": "Hull sealed",
                 "new_date": "2026-10-01", "reason": "x"}, ctx)
    monkeypatch.setattr(db, "flush", real_flush)
    db.rollback()

    assert _m(db, "Hull sealed").current_date == original, \
        "the date moved even though the event was not recorded"


def test_replan_without_a_reason_is_refused_at_the_tool(db, ctx, project):
    """NOT NULL at the column stops a direct write; this stops the TOOL from
    making an unexplained move easy."""
    _ratified(db, ctx)
    msg = _replan({"project": "Boat restoration", "milestone": "Hull sealed",
                   "new_date": "2026-10-01", "reason": "  "}, ctx)

    assert "Why is it moving" in msg
    assert db.query(Replan).count() == 0
    assert _m(db, "Hull sealed").current_date == date(2026, 9, 1)


def test_slippage_is_the_delta_in_days(db, ctx, project):
    _ratified(db, ctx)
    _replan({"project": "Boat restoration", "milestone": "Hull sealed",
             "new_date": "2026-09-13", "reason": "yard"}, ctx)

    assert slippage_days(_m(db, "Hull sealed"), today=date(2026, 8, 1)) == 12
    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "12 days past baseline" in out


def test_an_open_milestone_slips_by_today_not_by_its_plan(db, ctx, project):
    """A checkpoint due three weeks ago and not hit is three weeks past, not
    zero past — otherwise a stalled project reports as on-plan."""
    _ratified(db, ctx, when="2020-01-01")
    m = _m(db, "Hull sealed")
    assert slippage_days(m, today=date(2020, 1, 15)) == 14


def test_a_done_milestone_is_judged_on_its_plan_date(db, ctx, project):
    _ratified(db, ctx, when="2026-09-01")
    m = _m(db, "Hull sealed")
    m.status = "done"
    db.commit()
    assert slippage_days(m, today=date(2030, 1, 1)) == 0


def test_the_timeline_states_facts_and_never_a_verdict(db, ctx, project):
    """§6, guarded in test. The day count is observable and true; a verdict on
    the owner's work is an inference JARVIS does not assert."""
    _ratified(db, ctx, when="2020-01-01")
    out = _project_timeline({"project": "Boat restoration"}, ctx)

    assert "past baseline" in out
    assert re.search(r"\d+ days past baseline", out)
    low = out.lower()
    for word in JUDGMENT_WORDS:
        assert word not in low, f"the timeline passed judgment: {word!r}"


def test_the_timeline_marks_proposals_as_proposals(db, ctx, project):
    """A proposal rendered like a commitment defeats the stored guard — the
    surface must not flatten the distinction."""
    _ratified(db, ctx, "Hull sealed", "2026-09-01")
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Engine rebuilt",
                             "date": "2026-12-01"}, ctx)

    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "PROPOSED, not ratified" in out
    assert "baseline 2026-09-01" in out


def test_the_timeline_surfaces_open_risks_and_broken_assumptions(db, ctx, project):
    db.add(PlanRisk(project_id=project.id, description="Duffel live mode may not activate"))
    db.add(PlanRisk(project_id=project.id, description="retired one", status="retired"))
    db.add(PlanAssumption(project_id=project.id, description="the engine is sound",
                          status="broken"))
    db.commit()

    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "Duffel live mode" in out
    assert "retired one" not in out
    assert "the engine is sound" in out


# ── reset_baseline ───────────────────────────────────────────────────────────
def test_reset_baseline_snapshots_before_overwriting(db, ctx, project):
    """A re-baseline that isn't logged is indistinguishable from hiding a slip,
    so the snapshot happens first and captures the OLD line."""
    _ratified(db, ctx, when="2026-09-01")
    _replan({"project": "Boat restoration", "milestone": "Hull sealed",
             "new_date": "2026-10-01", "reason": "scope grew"}, ctx)

    _reset_baseline({"project": "Boat restoration", "reason": "the plan genuinely changed"}, ctx)

    snap = db.query(BaselineReset).one()
    assert "2026-09-01" in snap.snapshot, "the OLD baseline was not captured"
    assert "genuinely changed" in snap.reason
    assert _m(db, "Hull sealed").baseline_date == date(2026, 10, 1)


def test_reset_baseline_requires_a_reason(db, ctx, project):
    _ratified(db, ctx)
    msg = _reset_baseline({"project": "Boat restoration", "reason": ""}, ctx)
    assert "hiding a slip" in msg
    assert db.query(BaselineReset).count() == 0


# ── Registration ─────────────────────────────────────────────────────────────
def test_the_tools_are_registered_ungated_and_voice_reachable():
    """Reversible bookkeeping on JARVIS's own records, and 'where am I on X' is
    a question asked from a boat."""
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    reg = build_registry(include_delegate=False)
    for name in ("propose_milestone_date", "ratify_plan", "replan",
                 "reset_baseline", "project_timeline"):
        assert reg.has(name)
        assert not reg.is_gated(name)
        assert name in VOICE_TOOLS_PHASE1


def test_a_bad_date_is_refused_not_guessed(db, ctx, project):
    """A date guessed from ambiguous input is the same class of fabrication the
    whole module exists to prevent."""
    msg = _propose_milestone_date({"project": "Boat restoration",
                                   "milestone": "Hull sealed", "date": "next tuesday"}, ctx)
    assert "couldn't read" in msg
    assert _m(db, "Hull sealed").current_date is None
