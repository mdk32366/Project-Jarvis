"""Project & milestone tracking — TDD #1 (docs/TDD-project-tracking.md §10).

The through-line: the record must not quietly lie. Most of what is asserted here
is that progress cannot be overstated (dropped ≠ done), that state which needs a
reason cannot be set without one (parked), and that ambiguity ASKS rather than
guessing — because completing the wrong milestone is a silent data error that
looks like progress.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.handlers.base import Context
from app.handlers.projects import (
    _add_milestone, _attach_document, _complete_milestone, _create_project,
    _drop_milestone, _list_projects, _project_status, _promote_idea,
    _set_project_status, _supersede_document,
)
from app.models import Idea, Milestone, Project, ProjectDocument


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t")


def _mk(ctx, name="Location Pull", summary="s"):
    _create_project({"name": name, "summary": summary}, ctx)
    return ctx.db.query(Project).filter(Project.name == name).one()


# ── promotion preserves the idea ─────────────────────────────────────────────

def test_promotion_preserves_the_idea(ctx):
    """Promotion is a status change plus a link — NEVER a move or a delete. The
    origin of a project is part of its history."""
    idea = Idea(title="Boat maintenance log", body="track engine hours")
    ctx.db.add(idea)
    ctx.db.commit()

    out = _promote_idea({"idea_id": idea.id, "project_name": "Engine Log"}, ctx)
    assert "promoted" in out.lower()

    ctx.db.expire_all()
    still = ctx.db.get(Idea, idea.id)
    assert still is not None                       # NOT deleted
    assert still.status == "promoted"
    assert still.title == "Boat maintenance log"   # NOT rewritten

    p = ctx.db.query(Project).filter(Project.name == "Engine Log").one()
    assert p.idea_id == idea.id


def test_promoting_the_same_idea_twice_is_refused(ctx):
    idea = Idea(title="Second thoughts")
    ctx.db.add(idea)
    ctx.db.commit()
    _promote_idea({"idea_id": idea.id, "project_name": "First"}, ctx)
    out = _promote_idea({"idea_id": idea.id, "project_name": "Second"}, ctx)
    assert "already" in out.lower()
    assert ctx.db.query(Project).count() == 1


def test_ambiguous_idea_title_asks(ctx):
    ctx.db.add(Idea(title="boat cover replacement"))
    ctx.db.add(Idea(title="boat cover cleaning"))
    ctx.db.commit()
    out = _promote_idea({"title": "boat cover"}, ctx)
    assert "which one" in out.lower()
    assert ctx.db.query(Project).count() == 0      # nothing written


# ── parked requires a reason ─────────────────────────────────────────────────

def test_parking_without_a_reason_is_refused(ctx):
    """Parked-with-a-reason tells you when to look again. Parked-without is
    indistinguishable from abandoned."""
    p = _mk(ctx)
    out = _set_project_status({"project": p.name, "status": "parked"}, ctx)
    assert "reason" in out.lower()
    ctx.db.expire_all()
    assert ctx.db.get(Project, p.id).status == "active"      # unchanged


def test_parking_with_a_reason_succeeds_and_is_retrievable(ctx):
    p = _mk(ctx)
    _set_project_status(
        {"project": p.name, "status": "parked",
         "reason": "until the false-positive rate is known"}, ctx)
    ctx.db.expire_all()
    row = ctx.db.get(Project, p.id)
    assert row.status == "parked"
    assert "false-positive" in row.parked_reason
    assert "false-positive" in _project_status({"project": p.name}, ctx)


def test_terminal_statuses_stamp_completed_at(ctx):
    p = _mk(ctx)
    _set_project_status({"project": p.name, "status": "abandoned"}, ctx)
    ctx.db.expire_all()
    row = ctx.db.get(Project, p.id)
    assert row.status == "abandoned" and row.completed_at is not None


def test_unparking_clears_the_reason(ctx):
    p = _mk(ctx)
    _set_project_status({"project": p.name, "status": "parked", "reason": "waiting"}, ctx)
    _set_project_status({"project": p.name, "status": "active"}, ctx)
    ctx.db.expire_all()
    assert ctx.db.get(Project, p.id).parked_reason == ""


# ── milestone ordering ───────────────────────────────────────────────────────

def test_after_inserts_between_without_renumbering(ctx):
    p = _mk(ctx)
    for t in ("design", "build", "verify"):
        _add_milestone({"project": p.name, "title": t}, ctx)
    before = {m.title: m.position
              for m in ctx.db.query(Milestone).filter(Milestone.project_id == p.id)}

    _add_milestone({"project": p.name, "title": "migrate", "after": "design"}, ctx)

    rows = ctx.db.query(Milestone).filter(Milestone.project_id == p.id).order_by(
        Milestone.position).all()
    assert [m.title for m in rows] == ["design", "migrate", "build", "verify"]
    after = {m.title: m.position for m in rows}
    for t in ("design", "build", "verify"):
        assert after[t] == before[t]               # untouched


# ── completion ───────────────────────────────────────────────────────────────

def test_completion_is_idempotent(ctx):
    """The moment it was actually finished is the fact worth keeping."""
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    _complete_milestone({"project": p.name, "milestone": "design"}, ctx)
    ctx.db.expire_all()
    first = ctx.db.query(Milestone).filter(Milestone.project_id == p.id).one().completed_at

    out = _complete_milestone({"project": p.name, "milestone": "design"}, ctx)
    assert "already" in out.lower()
    ctx.db.expire_all()
    assert ctx.db.query(Milestone).filter(Milestone.project_id == p.id).one().completed_at == first


def test_ambiguous_milestone_asks_and_writes_nothing(ctx):
    """Completing the wrong milestone is a silent data error that looks like
    progress — worse than a clumsy question."""
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "health check split"}, ctx)
    _add_milestone({"project": p.name, "title": "health check seed"}, ctx)

    out = _complete_milestone({"project": p.name, "milestone": "health check"}, ctx)
    assert "which one" in out.lower()
    ctx.db.expire_all()
    assert all(m.status == "open"
               for m in ctx.db.query(Milestone).filter(Milestone.project_id == p.id))


def test_dropped_does_not_count_as_done(ctx):
    """A milestone that stopped being relevant was not achieved. Collapsing the
    two overstates progress."""
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    _add_milestone({"project": p.name, "title": "obsolete step"}, ctx)
    _complete_milestone({"project": p.name, "milestone": "design"}, ctx)
    _drop_milestone({"project": p.name, "milestone": "obsolete", "reason": "scope changed"}, ctx)

    out = _project_status({"project": p.name}, ctx)
    assert "1/1 done" in out                        # not 1/2, and not 2/2


def test_dropping_requires_a_reason(ctx):
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    out = _drop_milestone({"project": p.name, "milestone": "design"}, ctx)
    assert "reason" in out.lower()
    ctx.db.expire_all()
    assert ctx.db.query(Milestone).filter(Milestone.project_id == p.id).one().status == "open"


# ── cascade ──────────────────────────────────────────────────────────────────

def test_deleting_a_project_removes_its_children(ctx):
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    _attach_document({"project": p.name, "title": "TDD", "kind": "tdd"}, ctx)

    ctx.db.delete(ctx.db.get(Project, p.id))
    ctx.db.commit()
    assert ctx.db.query(Milestone).count() == 0
    assert ctx.db.query(ProjectDocument).count() == 0


# ── documents & the live-singular rule ───────────────────────────────────────

def test_two_live_docs_of_one_kind_is_surfaced(ctx):
    """'What's the design for X' must return the live TDD, SINGULAR."""
    p = _mk(ctx)
    _attach_document({"project": p.name, "title": "TDD v1", "kind": "tdd"}, ctx)
    out = _attach_document({"project": p.name, "title": "TDD v2", "kind": "tdd"}, ctx)
    assert "2 live 'tdd'" in out
    assert "2 live 'tdd'" in _project_status({"project": p.name}, ctx)


def test_supersede_archives_the_old_and_records_what_replaced_it(ctx):
    p = _mk(ctx)
    _attach_document({"project": p.name, "title": "TDD v1", "kind": "tdd"}, ctx)
    _attach_document({"project": p.name, "title": "TDD v2", "kind": "tdd"}, ctx)
    _supersede_document(
        {"project": p.name, "document": "TDD v1", "superseded_by": "TDD v2"}, ctx)

    ctx.db.expire_all()
    old = ctx.db.query(ProjectDocument).filter(ProjectDocument.title == "TDD v1").one()
    new = ctx.db.query(ProjectDocument).filter(ProjectDocument.title == "TDD v2").one()
    assert old.tier == "archive" and old.superseded_by_id == new.id
    assert "2 live" not in _project_status({"project": p.name}, ctx)


def test_tier_must_be_valid(ctx):
    p = _mk(ctx)
    out = _attach_document({"project": p.name, "title": "x", "tier": "wherever"}, ctx)
    assert "Tier must be" in out
    assert ctx.db.query(ProjectDocument).count() == 0


# ── exception-first reporting ────────────────────────────────────────────────

def test_active_project_with_no_milestones_is_flagged(ctx):
    p = _mk(ctx)
    assert "no milestones" in _project_status({"project": p.name}, ctx)


def test_active_project_with_everything_done_is_flagged(ctx):
    """Finished, or the record is stale. Either way it needs a human."""
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "only step"}, ctx)
    _complete_milestone({"project": p.name, "milestone": "only step"}, ctx)
    assert "every milestone is done" in _project_status({"project": p.name}, ctx)


def test_stale_active_project_is_flagged(ctx):
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    row = ctx.db.get(Project, p.id)
    row.updated_at = datetime.now(timezone.utc) - timedelta(days=45)
    ctx.db.commit()
    assert "untouched for 45 days" in _project_status({"project": p.name}, ctx)


def test_parked_project_is_not_nagged_about_staleness(ctx):
    """Parked is a deliberate state. Flagging it would train the eye to ignore
    the flag."""
    p = _mk(ctx)
    _set_project_status({"project": p.name, "status": "parked", "reason": "waiting"}, ctx)
    row = ctx.db.get(Project, p.id)
    row.updated_at = datetime.now(timezone.utc) - timedelta(days=90)
    ctx.db.commit()
    out = _project_status({"project": p.name}, ctx)
    assert "Needs attention" not in out


# ── listing & lookup ─────────────────────────────────────────────────────────

def test_list_defaults_to_active(ctx):
    _mk(ctx, "Alpha")
    _mk(ctx, "Beta")
    _set_project_status({"project": "Beta", "status": "done"}, ctx)
    out = _list_projects({}, ctx)
    assert "Alpha" in out and "Beta" not in out
    assert "Beta" in _list_projects({"status": "all"}, ctx)


def test_ambiguous_project_name_asks(ctx):
    _mk(ctx, "Location Pull Inversion")
    _mk(ctx, "Location Freshness")
    out = _project_status({"project": "Location"}, ctx)
    assert "which one" in out.lower()


def test_duplicate_project_name_refused(ctx):
    _mk(ctx, "Alpha")
    out = _create_project({"name": "alpha"}, ctx)      # case-insensitive
    assert "already" in out.lower()
    assert ctx.db.query(Project).count() == 1


# ── health check ─────────────────────────────────────────────────────────────

def test_hygiene_unknown_with_no_projects(db):
    """No evidence is not health."""
    from app.health import seed_health_topology
    from app.health_checks import check_project_hygiene
    from app.models import Component

    seed_health_topology(db)
    r = check_project_hygiene(db, db.get(Component, "project_hygiene"))
    assert r.status == "unknown" and r.fault_code == "no_projects"


def test_hygiene_degraded_never_down(ctx):
    """A bookkeeping problem must never render like a dead subsystem."""
    from app.health import seed_health_topology
    from app.health_checks import check_project_hygiene
    from app.models import Component

    seed_health_topology(ctx.db)
    _mk(ctx)                                          # active, no milestones
    r = check_project_hygiene(ctx.db, ctx.db.get(Component, "project_hygiene"))
    assert r.status == "degraded" and r.fault_code == "record_stale"
    assert r.status != "down"


def test_hygiene_ok_when_records_are_clean(ctx):
    from app.health import seed_health_topology
    from app.health_checks import check_project_hygiene
    from app.models import Component

    seed_health_topology(ctx.db)
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)
    _attach_document({"project": p.name, "title": "TDD", "kind": "tdd"}, ctx)
    r = check_project_hygiene(ctx.db, ctx.db.get(Component, "project_hygiene"))
    assert r.status == "ok"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_project_tools_are_registered_and_ungated():
    """Reversible bookkeeping must not dilute the confirmation gate."""
    from app.handlers.base import build_registry
    from app.handlers.projects import TOOL_NAMES

    reg = build_registry()
    for name in TOOL_NAMES:
        assert reg.has(name), name
        assert not reg.is_gated(name), name


def test_admin_read_model_exposes_progress_and_anomalies(client, auth_headers, ctx):
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)

    r = client.get("/api/projects", headers=auth_headers)
    assert r.status_code == 200
    row = r.json()["projects"][0]
    assert row["name"] == p.name and row["done"] == 0 and row["total"] == 1
    assert row["next"] == "design"
    assert "no live TDD attached" in row["anomalies"]


def test_admin_writes_go_through_the_tool_path(client, auth_headers, ctx):
    """The UI has no write endpoints of its own — a browser click and a phone call
    take the same path, so there is one write path and one set of tests."""
    p = _mk(ctx)
    _add_milestone({"project": p.name, "title": "design"}, ctx)

    r = client.post("/api/projects/action", headers=auth_headers,
                    json={"tool": "complete_milestone",
                          "args": {"project": p.name, "milestone": "design"}})
    assert r.status_code == 200 and r.json()["status"] == "ok"

    ctx.db.expire_all()
    assert ctx.db.query(Milestone).filter(Milestone.project_id == p.id).one().status == "done"


def test_admin_action_fails_closed_against_the_wider_registry(client, auth_headers):
    """This is not a general tool-invocation endpoint."""
    r = client.post("/api/projects/action", headers=auth_headers,
                    json={"tool": "send_email", "args": {}})
    assert r.status_code == 404


def test_admin_endpoints_require_auth(client):
    assert client.get("/api/projects").status_code in (401, 403)
    assert client.post("/api/projects/action",
                       json={"tool": "list_projects"}).status_code in (401, 403)


def test_project_tools_are_voice_reachable():
    """'Where am I on X' is asked from a boat. Also guards the standing trap:
    a secretary tool missing from VOICE_TOOLS_PHASE1 silently drops the WHOLE
    agent from voice."""
    from app.agents import DEFAULT_AGENTS
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
    from app.handlers.projects import TOOL_NAMES

    for name in TOOL_NAMES:
        assert name in VOICE_TOOLS_PHASE1, name
    assert set(DEFAULT_AGENTS["secretary"].tools) <= VOICE_TOOLS_PHASE1
