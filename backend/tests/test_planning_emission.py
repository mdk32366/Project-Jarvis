"""Emission — TDD #2 §7.1, §4.2, §8. The other half of the gate.

The sharpest test here is the same shape as #54's: a not-ready session must
reach **zero** GitHub calls. Not "it returned an error" — a writer that composes
and then checks readiness would pass that weaker test while having already built
the document it should have refused.
"""

import httpx
import pytest

from app.config import settings
from app.handlers.base import Context, build_registry
from app.handlers.planning import _emit_tdd
from app.models import PlanningNote, PlanningSession, Project, ProjectDocument
from app.planning import BANNER, compose_document

from test_planning_sessions import _complete, _session  # reuse the gate fixtures


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setattr("app.handlers.planning._classify",
                        lambda content, target="jarvis": None)


@pytest.fixture
def jarvis(db):
    p = Project(name="JARVIS", summary="herself", status="active")
    db.add(p)
    db.commit()
    return p


class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code, self._json, self.text = status_code, json_data or {}, text

    def json(self):
        return self._json


class _FakeGitHub:
    def __init__(self, sink):
        self.sink = sink

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
            self.sink["ref"] = json
            return _Resp(201, {})
        if url.endswith("/pulls"):
            self.sink["pr"] = json
            return _Resp(201, {"html_url": "https://github.com/mdk32366/Project-Jarvis/pull/77"})
        return _Resp(404)

    def put(self, url, headers=None, json=None):
        self.sink["calls"].append(("PUT", url))
        self.sink["put"] = json
        return _Resp(201, {"content": {"sha": "x"}})


def _install(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_test")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    sink: dict = {"calls": []}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeGitHub(sink))
    return sink


def _forbid(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_test")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")

    def _boom(*a, **k):
        raise AssertionError("GitHub client constructed — emission outran the gate")

    monkeypatch.setattr(httpx, "Client", _boom)


def _emitted_body(sink) -> str:
    import base64
    return base64.b64decode(sink["put"]["content"]).decode("utf-8")


# ── 1. THE PROOF: the gate stops emission before anything happens ────────────
def test_a_not_ready_session_refuses_and_never_calls_github(db, ctx, jarvis, monkeypatch):
    """THE GATE-BEFORE-EMISSION PROOF AT THE EMIT LAYER.

    Zero GitHub calls — not "it returned an error". A writer that composed the
    document first and checked readiness afterwards would satisfy the weaker
    assertion while having already built the thing it should have refused.
    """
    _forbid(monkeypatch)
    s = _session(db, {"problem": "TBD", "goals": "TBD"})

    result = _emit_tdd({}, ctx)

    assert "not writing that up yet" in result
    assert db.query(ProjectDocument).count() == 0
    db.refresh(s)
    assert s.status == "open", "a refused session stays open"
    assert s.emitted_at is None


def test_the_refusal_carries_the_missing_slots_and_their_questions(db, ctx, jarvis, monkeypatch):
    """Refusal is a feature only if it moves the session forward (§5.3)."""
    _forbid(monkeypatch)
    slots = _complete()
    slots.pop("rejected")
    _session(db, slots)

    result = _emit_tdd({}, ctx)

    assert "rejected" in result
    assert "why did it lose" in result.lower()


def test_emission_calls_the_gate_rather_than_reimplementing_it(db, ctx, jarvis, monkeypatch):
    """A second copy of the readiness rules is a second thing that can disagree
    with the first — and the one that gets fixed is whichever is failing loudly.
    Patching the shared gate must change emission's behaviour."""
    _forbid(monkeypatch)
    _session(db, _complete())

    import app.handlers.planning as planning_mod
    monkeypatch.setattr(planning_mod, "session_readiness",
                        lambda notes, **kw: type("R", (), {
                            "ready": False, "summary": lambda self: "STUBBED REFUSAL"})())

    assert "STUBBED REFUSAL" in _emit_tdd({}, ctx)


# ── 2. The banner (§7.1.2) ───────────────────────────────────────────────────
def test_the_banner_is_verbatim_and_first(db, ctx, jarvis, monkeypatch):
    """A document that LOOKS build-ready and isn't is worse than no document.
    The banner is the KEEL boundary made visible in the artifact, so it is
    asserted as exact text at the very top — not 'contains something similar'."""
    sink = _install(monkeypatch)
    _session(db, _complete())

    _emit_tdd({}, ctx)
    body = _emitted_body(sink)

    assert body.startswith("> Drafted in a JARVIS planning session on ")
    assert "Planner-ready, NOT\n> build-ready — bring to a design session before implementation." in body
    # The exact template, with only the date substituted.
    first_two = "\n".join(body.splitlines()[:2])
    assert first_two == BANNER.format(date=body.splitlines()[0].split(" on ")[1].split(".")[0])


def test_the_banner_cannot_be_omitted(db):
    """Asserted at the composer, not just the emit path — so a future caller
    that composes directly still gets it."""
    s = _session(db, _complete())
    doc = compose_document(s, s.notes, date="2026-08-01")
    assert doc.startswith(BANNER.format(date="2026-08-01"))


# ── 3. Provenance (§7.1.3) ───────────────────────────────────────────────────
def test_provenance_is_present_and_accurate(db, ctx, jarvis, monkeypatch):
    """Provenance pairs with the gate: the gate refuses thin content, provenance
    makes thin content VISIBLE even when it passes. Four notes in one sitting and
    thirty across three channels read identically without it."""
    sink = _install(monkeypatch)
    s = _session(db, _complete())
    db.add(PlanningNote(session_id=s.id, slot="problem", content="from the dock", channel="sms"))
    db.add(PlanningNote(session_id=s.id, slot="problem", content="on the drive", channel="voice"))
    db.commit()
    db.refresh(s)
    n = len(s.notes)

    _emit_tdd({}, ctx)
    body = _emitted_body(sink)

    assert "## Provenance" in body
    assert f"- Planning session: #{s.id}" in body
    assert f"- Notes captured: {n}" in body
    for channel in ("sms", "voice", "web"):
        assert channel in body.split("## Provenance", 1)[1]


# ── 4. Never main (§7.1) ─────────────────────────────────────────────────────
def test_emission_never_targets_main(db, ctx, jarvis, monkeypatch):
    sink = _install(monkeypatch)
    _session(db, _complete())

    _emit_tdd({}, ctx)

    assert sink["ref"]["ref"].startswith("refs/heads/docs/")
    assert sink["put"]["branch"].startswith("docs/")
    assert sink["put"]["branch"] != "main"
    assert sink["pr"]["base"] == "main" and sink["pr"]["head"].startswith("docs/")
    for _, url in sink["calls"]:
        assert "/merge" not in url


# ── 5. Voice cannot emit (§4.2) ──────────────────────────────────────────────
def test_emit_tdd_is_structurally_excluded_from_voice():
    """Reviewing a design by having it read aloud is not review — and emission
    opens a PR on a repo that is public by default. Absent from the allowlist AND
    from a voice-restricted registry: fail-closed, not filtered."""
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    assert "emit_tdd" not in VOICE_TOOLS_PHASE1
    voice = build_registry(include_delegate=True, allow=VOICE_TOOLS_PHASE1)
    assert not voice.has("emit_tdd")


def test_emit_tdd_is_top_level_and_on_no_agent_roster():
    from app.agents import DEFAULT_AGENTS

    assert build_registry(include_delegate=True).has("emit_tdd")
    assert not build_registry(include_delegate=False).has("emit_tdd")
    for name, a in DEFAULT_AGENTS.items():
        assert "emit_tdd" not in a.tools, f"{name} would drag emit_tdd onto voice"


# ── 6. Scan before write ─────────────────────────────────────────────────────
def test_a_secret_in_a_note_aborts_emission(db, ctx, jarvis, monkeypatch):
    """Notes come from SMS and voice; a pasted credential is the realistic way
    one arrives. Emission owns this refusal rather than pattern-matching
    commit_document's prose — deciding state from another tool's wording is the
    proxy-signal defect one layer up."""
    _forbid(monkeypatch)
    slots = _complete()
    slots["approach"] = slots["approach"] + " key sk-ant-" + "api03-AAAAbbbbCCCCddddEEEE"
    s = _session(db, slots)

    result = _emit_tdd({}, ctx)

    assert "won't publish" in result
    assert "sk-ant-api03-AAAAbbbbCCCCddddEEEE" not in result
    db.refresh(s)
    assert s.status == "open"


# ── 7. Happy path + the double-attach trap ───────────────────────────────────
def test_a_ready_session_emits_once_and_is_marked_emitted(db, ctx, jarvis, monkeypatch):
    sink = _install(monkeypatch)
    s = _session(db, _complete())

    result = _emit_tdd({}, ctx)

    assert "pull/77" in result
    db.refresh(s)
    assert s.status == "emitted" and s.emitted_at is not None
    assert s.document_id is not None


def test_exactly_one_project_document_is_created(db, ctx, jarvis, monkeypatch):
    """THE TRAP THE ORDER'S PIPELINE WOULD HAVE SET. `commit_document` already
    calls `attach_document` internally (#54), so emission attaching again would
    insert a SECOND ProjectDocument for one document — and two live docs of one
    kind on a project is precisely the anomaly `project_hygiene` reports."""
    _install(monkeypatch)
    _session(db, _complete())

    _emit_tdd({}, ctx)

    docs = db.query(ProjectDocument).all()
    assert len(docs) == 1, f"expected one document row, got {len(docs)}"
    assert docs[0].kind == "tdd" and docs[0].tier == "live"


def test_a_failed_write_leaves_the_session_open(db, ctx, jarvis, monkeypatch):
    """Success is judged on STATE (a new document row), never on the returned
    prose — so a write that didn't land cannot mark a session emitted."""
    monkeypatch.setattr(settings, "github_token", "")     # commit_document refuses
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    s = _session(db, _complete())

    result = _emit_tdd({}, ctx)

    assert "didn't land" in result
    db.refresh(s)
    assert s.status == "open" and s.document_id is None


def test_a_new_project_session_without_a_link_refuses(db, ctx, monkeypatch):
    """Never guess a repo — the same rule commit_document holds, applied one
    layer up so the failure is legible."""
    _forbid(monkeypatch)
    s = _session(db, _complete())
    s.target = "new_project"
    db.commit()

    assert "won't guess a repo" in _emit_tdd({}, ctx)


# ── 8. next_planning_question returns ONE (§4.3) ─────────────────────────────
def test_next_planning_question_returns_one_question_not_a_dump(db, ctx, jarvis):
    from app.handlers.planning import _next_planning_question, _planning_status

    slots = _complete()
    slots.pop("rejected")
    slots.pop("open_questions")
    _session(db, slots)

    q = _next_planning_question({}, ctx)
    status = _planning_status({}, ctx)

    assert q.count("?") <= 2, "one focused question, not a dump"
    assert len(q) < len(status), "the dump is planning_status's job"


# ── 9. Health check (§8) ─────────────────────────────────────────────────────
def test_planning_health_never_reports_down(db):
    from app.health import seed_health_topology
    from app.health_checks import check_planning_sessions
    from app.models import Component

    seed_health_topology(db)
    c = db.query(Component).filter_by(name="planning_sessions").one()

    assert check_planning_sessions(db, c).status == "unknown"   # none ever

    _session(db, _complete())
    assert check_planning_sessions(db, c).status == "ok"

    _session(db, {})                                            # a second open
    r = check_planning_sessions(db, c)
    assert r.status == "degraded" and r.fault_code == "session_stalled"
    assert "open at once" in r.detail


def test_planning_health_runbooks_join(db):
    from app.health import get_runbook, seed_health_topology

    seed_health_topology(db)
    for code in ("session_stalled", "no_evidence"):
        assert get_runbook(db, "planning_sessions", code) is not None
