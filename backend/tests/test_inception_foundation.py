"""Inception steps 1-2: the schema, and `project_plan` on #2's engine.

SCHEMA ONLY in step 1 — the columns exist, nothing moves them. Dates are not
set until step 3, and a test here that made one move would be behaviour running
ahead of its step.

The load-bearing test in step 2 is the LAST one: that #2's existing session
types still gate on their own slot set. Inception extends the engine; an
extension that widened the base slot set would have weakened the thing it was
built on, and that regression would be invisible from inception's own tests.
"""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.handlers.base import Context
from app.models import (BaselineReset, Milestone, PlanAssumption, PlanningNote,
                        PlanningSession, PlanRisk, Project, Replan)
from app.planning import (PROJECT_PLAN_REQUIRED, REQUIRED_SLOTS, session_readiness,
                          slot_set_for)

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setattr("app.handlers.planning._classify",
                        lambda content, target="jarvis": None)


@pytest.fixture
def project(db):
    p = Project(name="Boat restoration", summary="the arc", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _milestone(db, project) -> Milestone:
    m = Milestone(project_id=project.id, title="Hull sealed", position=10)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# ── STEP 1: schema ───────────────────────────────────────────────────────────
def test_milestone_date_columns_exist_and_default_correctly(db, project):
    """Nullable, and `date_status` starts at `none` — because a milestone that
    has never been discussed has no date and no claim about one."""
    m = _milestone(db, project)
    assert m.baseline_date is None
    assert m.current_date is None
    assert m.date_status == "none"


def test_the_date_columns_accept_their_values(db, project):
    m = _milestone(db, project)
    m.current_date = date(2026, 9, 1)
    m.baseline_date = date(2026, 8, 15)
    m.date_status = "ratified"
    db.commit()
    db.refresh(m)
    assert m.current_date == date(2026, 9, 1)
    assert m.baseline_date == date(2026, 8, 15)
    assert m.date_status == "ratified"


def test_replan_reason_is_not_null_at_the_database_layer(db, project):
    """STRUCTURAL, not tool-level (§4.4). A replan with no reason is exactly the
    silent field-edit this table exists to prevent, and enforcing it only in the
    tool leaves the silent edit one direct write away."""
    m = _milestone(db, project)
    db.add(Replan(milestone_id=m.id, from_date=date(2026, 8, 1),
                  to_date=date(2026, 9, 1), reason=None))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_baseline_reset_reason_is_not_null_at_the_database_layer(db, project):
    db.add(BaselineReset(project_id=project.id, snapshot="{}", reason=None))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_replan_records_from_to_and_why(db, project):
    """'Why did milestone 4 slip three weeks' is answerable only because the move
    was captured as an event with all three."""
    m = _milestone(db, project)
    db.add(Replan(milestone_id=m.id, from_date=date(2026, 8, 1),
                  to_date=date(2026, 9, 1), reason="the yard lost the slot"))
    db.commit()

    r = db.query(Replan).one()
    assert r.from_date == date(2026, 8, 1) and r.to_date == date(2026, 9, 1)
    assert "yard" in r.reason
    assert r.created_at is not None


def test_risk_and_assumption_defaults_and_optional_milestone_link(db, project):
    """A risk may threaten the whole project rather than one checkpoint, so the
    milestone link is nullable — forcing it would invent precision."""
    m = _milestone(db, project)
    db.add(PlanRisk(project_id=project.id, description="Duffel live mode may not activate"))
    db.add(PlanRisk(project_id=project.id, milestone_id=m.id, description="yard slot"))
    db.add(PlanAssumption(project_id=project.id, description="the engine is sound"))
    db.commit()

    risks = db.query(PlanRisk).order_by(PlanRisk.id).all()
    assert risks[0].status == "open" and risks[0].milestone_id is None
    assert risks[1].milestone_id == m.id
    assert db.query(PlanAssumption).one().status == "holding"


def test_retired_is_not_realized(db, project):
    """A risk that stopped applying is a different outcome from one that came
    true; collapsing them would overstate how much went wrong."""
    db.add(PlanRisk(project_id=project.id, description="x", status="retired"))
    db.add(PlanRisk(project_id=project.id, description="y", status="realized"))
    db.commit()
    assert {r.status for r in db.query(PlanRisk).all()} == {"retired", "realized"}


def _alembic(db_path: Path, *args: str):
    env = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300)


def test_inception_migration_roundtrips(tmp_path):
    """To THIS revision, not `head` — pinning the global head makes a
    per-migration test fail on the next migration."""
    db_file = tmp_path / "m.db"
    up = _alembic(db_file, "upgrade", "0028_inception")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    engine = create_engine(f"sqlite+pysqlite:///{db_file}")
    try:
        insp = inspect(engine)
        for t in ("replan", "baseline_reset", "plan_risk", "plan_assumption"):
            assert t in insp.get_table_names(), f"{t} not created"
        cols = {c["name"] for c in insp.get_columns("milestone")}
        assert {"baseline_date", "current_date", "date_status"} <= cols
        with engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one() \
                == "0028_inception"
    finally:
        engine.dispose()

    down = _alembic(db_file, "downgrade", "-1")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"

    engine = create_engine(f"sqlite+pysqlite:///{db_file}")
    try:
        insp = inspect(engine)
        for t in ("replan", "baseline_reset", "plan_risk", "plan_assumption"):
            assert t not in insp.get_table_names(), f"{t} survived downgrade"
        cols = {c["name"] for c in insp.get_columns("milestone")}
        assert not ({"baseline_date", "current_date", "date_status"} & cols)
    finally:
        engine.dispose()


def test_migration_chains_off_the_confirmed_head():
    """The TDD said 0026 — consumed by the github write log. Fifth stale number
    in this series, which is why the rule is confirm-never-trust."""
    mig = (BACKEND / "alembic" / "versions" / "0028_inception.py").read_text(encoding="utf-8")
    assert 'revision = "0028_inception"' in mig
    assert 'down_revision = "0027_planning_sessions"' in mig


# ── STEP 2: project_plan on #2's engine ──────────────────────────────────────
def _real(n: int = 160) -> str:
    base = ("The yard needs the hull sealed before the weather turns, and the window "
            "for that is narrower than it looks because the paint needs three dry days. ")
    return (base * 3)[:n]


def _plan_slots() -> dict[str, str]:
    d = {s: _real() for s in PROJECT_PLAN_REQUIRED}
    d["rejected"] = ("Considered doing the deck first, but the hull work has a weather "
                     "window and the deck does not, so it lost on sequencing alone.")
    d["milestones"] = f"{_real(140)}\n\nSecond checkpoint: {_real(140)}"
    d["tasks"] = _real()
    return d


def _session(db, target: str, slots: dict[str, str]) -> PlanningSession:
    s = PlanningSession(topic="Restore the boat", target=target, status="open")
    db.add(s)
    db.commit()
    db.refresh(s)
    for slot, content in slots.items():
        db.add(PlanningNote(session_id=s.id, slot=slot, content=content, channel="web"))
    db.commit()
    db.refresh(s)
    return s


def test_start_planning_opens_a_project_plan_session_on_the_same_table(db, ctx):
    """A session TYPE, not a second engine — same table, same accumulation."""
    from app.handlers.planning import _start_planning

    msg = _start_planning({"topic": "Restore the boat", "target": "project_plan"}, ctx)

    s = db.query(PlanningSession).one()
    assert s.target == "project_plan"
    assert "project_plan" in msg
    assert db.query(PlanningSession).count() == 1


def test_a_complete_project_plan_is_ready(db):
    s = _session(db, "project_plan", _plan_slots())
    assert session_readiness(s.notes, target=s.target).ready


def test_empty_risks_refuses_and_says_why(db):
    """The inception analogue of #2's empty-`rejected`: you cannot generate a
    real risk from a project name."""
    slots = _plan_slots()
    slots.pop("risks")
    r = session_readiness(_session(db, "project_plan", slots).notes, target="project_plan")

    assert not r.ready
    assert [v.slot for v in r.missing] == ["risks"]
    assert "nobody stress-tested" in r.summary()


def test_empty_assumptions_refuses_and_says_why(db):
    slots = _plan_slots()
    slots.pop("assumptions")
    r = session_readiness(_session(db, "project_plan", slots).notes, target="project_plan")

    assert not r.ready
    assert [v.slot for v in r.missing] == ["assumptions"]
    assert "break the plan" in r.summary()


def test_a_placeholder_risk_is_empty_not_present(db):
    """Substance, not presence — the same check #2 applies, inherited rather than
    re-implemented. A risk of 'TBD' is not a risk."""
    slots = _plan_slots()
    slots["risks"] = "TBD. To be determined. [details to follow] <fill in>"
    r = session_readiness(_session(db, "project_plan", slots).notes, target="project_plan")

    assert not r.ready
    assert [v.slot for v in r.missing] == ["risks"]


def test_fewer_than_two_milestones_refuses(db):
    """A plan with one checkpoint is a deadline, not a timeline (§4.2)."""
    slots = _plan_slots()
    slots["milestones"] = _real(200)          # one entry, long enough to pass length
    r = session_readiness(_session(db, "project_plan", slots).notes, target="project_plan")

    assert not r.ready
    assert [v.slot for v in r.missing] == ["milestones"]
    assert "fewer than two" in r.missing[0].reason


def test_tasks_may_be_explicitly_none_yet(db):
    """`tasks` is the conditional slot, and it inherits `data_model`'s rule
    generically rather than through a second copy: an explicit 'none yet' is a
    statement, silence is not."""
    slots = _plan_slots()
    slots["tasks"] = "none yet"
    assert session_readiness(_session(db, "project_plan", slots).notes,
                             target="project_plan").ready

    slots.pop("tasks")
    r = session_readiness(_session(db, "project_plan", slots).notes, target="project_plan")
    assert not r.ready and [v.slot for v in r.missing] == ["tasks"]


def test_a_project_plan_is_not_judged_on_the_technical_slots(db):
    """`tests` and `data_model` are deliberately NOT required for a project plan.
    Requiring a test plan for 'restore the boat' is a bar that gets routed around
    rather than met."""
    st = slot_set_for("project_plan")
    assert "tests" not in st.all and "data_model" not in st.all
    assert {"objectives", "milestones", "risks", "assumptions"} <= set(st.required)


# ── THE REGRESSION GUARD — inception must not weaken what it extends ─────────
def test_the_base_session_types_still_gate_on_their_own_slot_set(db):
    """THE LOAD-BEARING TEST OF THIS PR.

    Inception extends the engine. An extension that widened the base slot set,
    or let a project-plan slot satisfy a `jarvis` session, would have WEAKENED
    the thing it was built on — and that regression is invisible from
    inception's own tests, because they'd all still pass.
    """
    for target in ("jarvis", "new_project"):
        st = slot_set_for(target)
        assert st.required == REQUIRED_SLOTS, f"{target}'s required slots moved"
        assert "risks" not in st.all and "objectives" not in st.all

    # And behaviourally: a session filled with PROJECT-PLAN content does not
    # satisfy a `jarvis` session, because the technical slots are still required.
    s = _session(db, "jarvis", _plan_slots())
    r = session_readiness(s.notes, target="jarvis")
    assert not r.ready
    assert {"tests", "data_model"} <= {v.slot for v in r.missing}


def test_an_unknown_session_type_falls_back_to_the_stricter_set():
    """An unrecognised type is a bug, and a bug must not silently relax a gate."""
    assert slot_set_for("nonsense").required == REQUIRED_SLOTS
    assert slot_set_for(None).required == REQUIRED_SLOTS
