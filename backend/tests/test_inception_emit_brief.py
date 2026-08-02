"""Inception steps 5-7: resurfacing, ATOMIC EMIT, and the brief.

The sharpest test in this file is `test_a_failed_commit_leaves_no_half_landed_state`.
Everything else in step 6 is plumbing around the one design question §11 left
open: a GitHub PR and a DB transaction cannot share a transaction, so what
happens when the second half fails?
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.handlers.base import Context, build_registry
from app.handlers.inception import (_break_assumption, _emit_project_plan, _flag_risk,
                                    _project_timeline, _propose_milestone_date,
                                    _ratify_plan, _resolve_risk)
from app.inception import JUDGMENT_WORDS, brief_project_lines, mark_assumptions_surfaced
from app.models import (Milestone, PlanAssumption, PlanningNote, PlanningSession,
                        PlanRisk, Project, ProjectDocument)

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
    p = Project(name="Boat restoration", summary="the arc", status="active",
                repo_url="https://github.com/mdk32366/boat")
    db.add(p)
    db.commit()
    db.refresh(p)
    db.add(Milestone(project_id=p.id, title="Hull sealed", position=10))
    db.commit()
    db.refresh(p)
    return p


# ── GitHub fake ──────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code, self._json, self.text = status_code, json_data or {}, text

    def json(self):
        return self._json


class _FakeGitHub:
    def __init__(self, sink, put_status=201):
        self.sink, self.put_status = sink, put_status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        self.sink["calls"].append(("GET", url))
        if "/git/ref/heads/" in url:
            return _Resp(200, {"object": {"sha": "base"}})
        if "/contents/" in url:
            return _Resp(404)
        if url.split("/repos/", 1)[-1].count("/") == 1:
            return _Resp(200, {"default_branch": "main"})
        return _Resp(404)

    def post(self, url, headers=None, json=None):
        self.sink["calls"].append(("POST", url))
        if url.endswith("/git/refs"):
            return _Resp(201, {})
        if url.endswith("/pulls"):
            self.sink["pr"] = json
            return _Resp(201, {"html_url": "https://github.com/mdk32366/boat/pull/5"})
        return _Resp(404)

    def put(self, url, headers=None, json=None):
        self.sink["calls"].append(("PUT", url))
        self.sink["put"] = json
        return _Resp(self.put_status, {"content": {"sha": "x"}})


def _install(monkeypatch, **kw):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_test")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    sink: dict = {"calls": []}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeGitHub(sink, **kw))
    return sink


def _forbid(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_test")

    def _boom(*a, **k):
        raise AssertionError("GitHub client constructed — emit outran the gate")

    monkeypatch.setattr(httpx, "Client", _boom)


def _real(n: int = 160) -> str:
    base = ("The yard needs the hull sealed before the weather turns, and the window for "
            "that is narrower than it looks because the paint needs three dry days. ")
    return (base * 3)[:n]


def _plan_session(db, project, *, complete=True) -> PlanningSession:
    s = PlanningSession(topic="Restore the boat", target="project_plan",
                        project_id=project.id, status="open")
    db.add(s)
    db.commit()
    db.refresh(s)
    if not complete:
        db.add(PlanningNote(session_id=s.id, slot="problem", content="TBD", channel="web"))
        db.commit()
        db.refresh(s)
        return s

    slots = {s_: _real() for s_ in ("problem", "goals", "non_goals", "approach",
                                    "objectives", "tasks")}
    slots["rejected"] = ("Considered doing the deck first, but the hull work has a weather "
                         "window and the deck does not, so it lost on sequencing alone.")
    slots["open_questions"] = _real()
    slots["milestones"] = f"{_real(140)}\n\nSecond checkpoint: {_real(140)}"
    for slot, content in slots.items():
        db.add(PlanningNote(session_id=s.id, slot=slot, content=content, channel="web"))
    # Two risks and one assumption, each its own note — one note, one row.
    db.add(PlanningNote(session_id=s.id, slot="risks", channel="sms",
                        content="The yard may lose our slot if the weather turns early, and "
                                "there is no second yard within reach that can take a hull "
                                "this size before the spring."))
    db.add(PlanningNote(session_id=s.id, slot="risks", channel="web",
                        content="Paint may not cure in time for the sea trial window, which "
                                "would push the whole thing past the season and into next "
                                "year's haul-out schedule."))
    db.add(PlanningNote(session_id=s.id, slot="assumptions", channel="web",
                        content="We are assuming the engine is fundamentally sound and needs "
                                "only a service, because a rebuild would change both the "
                                "budget and the timeline completely."))
    db.commit()
    db.refresh(s)
    return s


# ── STEP 5 ───────────────────────────────────────────────────────────────────
def test_flag_risk_and_resolve_keep_realized_apart_from_retired(db, ctx, project):
    _flag_risk({"project": "Boat restoration", "description": "Duffel may not activate"}, ctx)
    r = db.query(PlanRisk).one()
    assert r.status == "open" and r.plan_status == "live"

    _resolve_risk({"risk_id": r.id, "outcome": "retired"}, ctx)
    db.refresh(r)
    assert r.status == "retired", "a risk that stopped applying is not one that came true"


def test_a_risk_linked_to_a_slipping_milestone_is_surfaced(db, ctx, project):
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2020-01-01"}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)
    _flag_risk({"project": "Boat restoration", "milestone": "Hull sealed",
                "description": "The yard may lose our slot"}, ctx)

    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "yard may lose our slot" in out

    lines = "\n".join(brief_project_lines(db, today=date(2020, 2, 1)))
    assert "now on a slipping milestone" in lines


def test_a_broken_assumption_surfaces_once(db, ctx, project):
    """§4.6. An assumption that turned out false is worth ONE flag — repeating it
    every morning trains the owner to skip the line, and the line is the value."""
    a = PlanAssumption(project_id=project.id, description="the engine is sound")
    db.add(a)
    db.commit()

    _break_assumption({"assumption_id": a.id}, ctx)
    first = brief_project_lines(db)
    assert any("no longer holds" in ln for ln in first)

    mark_assumptions_surfaced(db)                 # what delivery does
    second = brief_project_lines(db)
    assert not any("no longer holds" in ln for ln in second), "it re-alarmed"


def test_the_stamp_happens_on_delivery_not_on_compose(db, ctx, project):
    """A brief that fails to send must not consume the single flag the owner was
    owed — 'surfaced once' is worthless if the once went into a void."""
    a = PlanAssumption(project_id=project.id, description="x", status="broken")
    db.add(a)
    db.commit()

    brief_project_lines(db)
    brief_project_lines(db)
    db.refresh(a)
    assert a.surfaced_at is None, "composing consumed the flag"


# ── STEP 6: the gate, then atomicity ─────────────────────────────────────────
def test_a_not_ready_session_seeds_nothing_and_calls_no_github(db, ctx, project, monkeypatch):
    """THE GATE-BEFORE-EMIT PROOF at the inception layer: no rows, no commit."""
    _forbid(monkeypatch)
    s = _plan_session(db, project, complete=False)

    out = _emit_project_plan({}, ctx)

    assert "not writing that up yet" in out
    assert db.query(PlanRisk).count() == 0
    assert db.query(PlanAssumption).count() == 0
    assert db.query(ProjectDocument).count() == 0
    db.refresh(s)
    assert s.status == "open"


def test_emit_is_structurally_voice_excluded():
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    assert "emit_project_plan" not in VOICE_TOOLS_PHASE1
    assert not build_registry(include_delegate=True,
                              allow=VOICE_TOOLS_PHASE1).has("emit_project_plan")
    assert not build_registry(include_delegate=False).has("emit_project_plan")
    from app.agents import DEFAULT_AGENTS
    for name, a in DEFAULT_AGENTS.items():
        assert "emit_project_plan" not in a.tools, f"{name} would expose it to voice"


def test_atomic_success_seeds_then_commits_then_promotes(db, ctx, project, monkeypatch):
    sink = _install(monkeypatch)
    s = _plan_session(db, project)

    out = _emit_project_plan({}, ctx)

    assert "pull/5" in out
    risks = db.query(PlanRisk).all()
    asums = db.query(PlanAssumption).all()
    assert len(risks) == 2 and len(asums) == 1, "one note -> one row"
    assert all(r.plan_status == "live" for r in risks + asums), "rows were not promoted"
    assert "yard may lose our slot" in risks[0].description, "verbatim, not summarised"

    db.refresh(s)
    assert s.status == "emitted" and s.document_id is not None
    assert db.query(ProjectDocument).count() == 1


def test_a_failed_commit_leaves_no_half_landed_state(db, ctx, project, monkeypatch):
    """**THE SHARPEST TEST — the §11 design question, answered.**

    A GitHub PR and a DB transaction cannot share a transaction, so the failure
    mode to design against is a half-landed one. After a failed commit there
    must be NEITHER live rows without a document NOR a document without rows.
    """
    _install(monkeypatch, put_status=500)          # the content PUT fails
    s = _plan_session(db, project)

    out = _emit_project_plan({}, ctx)

    assert "didn't commit" in out and "nothing half-landed" in out
    assert db.query(ProjectDocument).count() == 0, "a document landed without rows"
    live = [r for r in db.query(PlanRisk).all() if r.plan_status == "live"]
    live += [a for a in db.query(PlanAssumption).all() if a.plan_status == "live"]
    assert live == [], "live rows survived a failed commit"
    assert db.query(PlanRisk).count() == 0 and db.query(PlanAssumption).count() == 0, \
        "draft rows were not cleaned up"

    db.refresh(s)
    assert s.status == "open" and s.document_id is None


def test_a_refused_commit_also_leaves_no_half_landed_state(db, ctx, project, monkeypatch):
    """THE SECOND FAILURE MODE, and the suite did not have it until a negative
    validation exposed the gap.

    `commit_document` can fail two ways: it can RAISE (a GitHub fault), or it
    can REFUSE by returning a human-facing string (no token, scanner hit). The
    raising path is handled by the try/except; this one is caught only by
    judging success on STATE — whether a new document row exists. A plant that
    promoted rows without that check survived the raise-path test untouched.
    """
    monkeypatch.setattr(settings, "github_token", "")      # commit_document refuses, no raise
    s = _plan_session(db, project)

    out = _emit_project_plan({}, ctx)

    assert "didn't commit" in out and "nothing half-landed" in out
    assert db.query(ProjectDocument).count() == 0
    assert db.query(PlanRisk).count() == 0 and db.query(PlanAssumption).count() == 0
    db.refresh(s)
    assert s.status == "open" and s.document_id is None


def test_an_orphaned_draft_is_visible_not_silent(db, ctx, project):
    """§11.8 applied to a two-system write: a partial must never masquerade as
    done."""
    db.add(PlanRisk(project_id=project.id, description="left over", plan_status="draft"))
    db.commit()

    out = _project_timeline({"project": "Boat restoration"}, ctx)
    assert "draft row(s) left over" in out
    assert "did not complete" in out


def test_emit_never_targets_main(db, ctx, project, monkeypatch):
    sink = _install(monkeypatch)
    _plan_session(db, project)
    _emit_project_plan({}, ctx)

    assert sink["put"]["branch"].startswith("docs/")
    assert sink["put"]["branch"] != "main"
    assert sink["pr"]["base"] == "main" and sink["pr"]["head"].startswith("docs/")
    for _, url in sink["calls"]:
        assert "/merge" not in url


def test_emit_refuses_a_project_with_no_repo_rather_than_creating_one(db, ctx, monkeypatch):
    """Repo creation stays behind ITS OWN gate. `create_project_repo` is gated,
    and the gate runs in the orchestrator — so calling it from inside this
    ungated tool would execute an irreversible outward action with no
    confirmation. Emission refuses and points at the gated tool."""
    _forbid(monkeypatch)
    p = Project(name="No repo yet", status="active", repo_url="")
    db.add(p)
    db.commit()
    _plan_session(db, p)

    out = _emit_project_plan({}, ctx)
    assert "gated action I can't take from inside an emit" in out


def test_the_plan_document_carries_the_banner_and_provenance(db, ctx, project, monkeypatch):
    import base64
    sink = _install(monkeypatch)
    s = _plan_session(db, project)
    _emit_project_plan({}, ctx)

    body = base64.b64decode(sink["put"]["content"]).decode("utf-8")
    assert body.startswith("> Drafted in a JARVIS planning session on ")
    assert "Planner-ready, NOT" in body
    assert "## Provenance" in body
    assert f"- Planning session: #{s.id}" in body
    assert "PROPOSALS until ratified" in body


# ── STEP 7: the brief ────────────────────────────────────────────────────────
def _ratified_slip(db, ctx, days: int):
    when = (date.today() - timedelta(days=days)).isoformat()
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": when}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)


def test_a_stalled_open_milestone_surfaces(db, ctx, project):
    """THE ANTI-FABRICATED-GREEN CASE. An open milestone past its date slips by
    TODAY's reckoning, so a frozen project surfaces rather than reporting
    on-plan."""
    _ratified_slip(db, ctx, days=30)
    lines = brief_project_lines(db)
    assert any("30 days past baseline" in ln for ln in lines)


def test_slippage_under_the_floor_is_silent(db, ctx, project):
    _ratified_slip(db, ctx, days=1)          # floor default is 2
    assert brief_project_lines(db) == []


def test_an_on_baseline_project_produces_no_line(db, ctx, project):
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": (date.today() + timedelta(days=30)).isoformat()}, ctx)
    _ratify_plan({"project": "Boat restoration"}, ctx)
    assert brief_project_lines(db) == []


def test_an_unratified_project_is_invisible_to_the_brief(db, ctx, project):
    """The step-3 fabrication guard, arriving at the brief layer: nothing agreed,
    nothing to slip from, nothing said."""
    _propose_milestone_date({"project": "Boat restoration", "milestone": "Hull sealed",
                             "date": "2020-01-01"}, ctx)
    assert brief_project_lines(db) == []


def test_the_brief_states_fact_and_never_a_verdict(db, ctx, project):
    _ratified_slip(db, ctx, days=45)
    text = " ".join(brief_project_lines(db)).lower()

    assert "days past baseline" in text
    for word in JUDGMENT_WORDS:
        assert word not in text, f"the brief passed judgment: {word!r}"


def test_the_brief_reuses_the_shared_judgment_guard_not_a_fork():
    """Two lists drift, and the one that drifts is the one nobody is looking at.
    `brief_project_lines` lives in the same module as the timeline's guard and
    the tests import ONE `JUDGMENT_WORDS`."""
    src = (BACKEND / "app" / "inception.py").read_text(encoding="utf-8")
    assert src.count("JUDGMENT_WORDS = ") == 1, "the judgment list was forked"
    brief_src = (BACKEND / "app" / "briefing.py").read_text(encoding="utf-8")
    assert "JUDGMENT_WORDS = " not in brief_src, "the brief defines its own copy"
    assert "JUDGMENT_WORDS" in brief_src, "the brief should POINT at the shared list"


def test_the_brief_section_appears_only_when_there_is_an_exception(db, ctx, project,
                                                                   monkeypatch):
    """Exception-first: silence is the default, so no '## Projects' heading at all
    when everything is on plan."""
    from app import briefing

    for name in ("_nws_weather", "_nws_marine", "_news_brief"):
        monkeypatch.setattr(briefing, name, lambda *a, **k: "")

    quiet = briefing.gather_context(db)
    assert "## Projects" not in quiet

    _ratified_slip(db, ctx, days=30)
    loud = briefing.gather_context(db)
    assert "## Projects" in loud and "30 days past baseline" in loud
