"""create_project_repo + the ratified uniform-public visibility flip.

TDD #3 §4.1, §4.3, §6.2 / build order step 6. Two things are load-bearing here
and both are asserted rather than trusted:

  1. The scanner runs BEFORE any repo exists. Public-by-default was ratified on
     the condition that nothing reaches a public repo unscanned, so a write path
     that could outrun the scan breaks the precondition the decision rests on.
  2. The gate's readback states the VISIBILITY, and says the same thing the code
     will actually do. A readback that disagrees with the action is worse than
     no readback: it manufactures consent for something else.

Fixture credentials are split across a `+` — GitHub push protection rejects
contiguous token-shaped literals.
"""

import httpx
import pytest

from app.config import settings
from app.handlers.base import Context, ToolFault, build_registry
from app.handlers.ideas import _create_project_from_idea, _summarize_promote
from app.handlers.repos import (_create_project_repo, _create_repo_pregate,
                                _summarize_create_repo)
from app.models import GithubWriteLog, Idea, Project


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture
def project(db):
    p = Project(name="Node Narrator", summary="Reads node status aloud.", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, sink, create_status=201, create_text="", put_status=201):
        self.sink = sink
        self.create_status = create_status
        self.create_text = create_text
        self.put_status = put_status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        self.sink["calls"].append(("POST", url))
        if url.endswith("/user/repos"):
            self.sink["create_json"] = json
            if self.create_status not in (200, 201):
                return _Resp(self.create_status, {}, self.create_text)
            return _Resp(201, {"full_name": f"mdk32366/{json['name']}",
                               "html_url": f"https://github.com/mdk32366/{json['name']}"})
        return _Resp(404)

    def get(self, url, headers=None, params=None):
        self.sink["calls"].append(("GET", url))
        return _Resp(200, {"html_url": "https://github.com/mdk32366/existing-thing"})

    def put(self, url, headers=None, json=None):
        self.sink["calls"].append(("PUT", url))
        self.sink.setdefault("puts", []).append(url.split("/contents/", 1)[-1])
        return _Resp(self.put_status if self.put_status in (200, 201) else self.put_status,
                     {"content": {"sha": "x"}})


def _install(monkeypatch, **kw):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_testtoken")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    sink: dict = {"calls": []}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(sink, **kw))
    return sink


def _forbid(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_testtoken")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")

    def _boom(*a, **k):
        raise AssertionError("GitHub client was constructed — a write outran the scan")

    monkeypatch.setattr(httpx, "Client", _boom)


# ── 1. Gated, with visibility in the readback ────────────────────────────────
def test_create_project_repo_is_gated_top_level_only():
    top = build_registry(include_delegate=True)
    assert top.has("create_project_repo")
    assert top.is_gated("create_project_repo"), "creating a named repo is irreversible"

    sub = build_registry(include_delegate=False)
    assert not sub.has("create_project_repo"), "sub-agents must not reach a gated tool"


def test_readback_states_name_visibility_and_owner():
    text = _summarize_create_repo({"name": "node-narrator", "project": "Node Narrator"})
    assert "public" in text
    assert "node-narrator" in text
    assert "mdk32366" in text, "the owner must be named — it is whose account it lands in"


def test_readback_reflects_an_explicit_private_request():
    text = _summarize_create_repo({"name": "secret-thing", "visibility": "private"})
    assert "private" in text and "public" not in text


def test_create_project_repo_is_not_voice_reachable():
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
    assert "create_project_repo" not in VOICE_TOOLS_PHASE1
    voice = build_registry(include_delegate=True, allow=VOICE_TOOLS_PHASE1)
    assert not voice.has("create_project_repo")


# ── 2. THE SAFETY PRECONDITION: scanner precedes creation ────────────────────
def test_scanner_precedes_creation_client_never_constructed(ctx, project, monkeypatch):
    """Public-by-default was ratified ON THE CONDITION that nothing reaches a
    public repo unscanned. So the assertion is not "it returned an error" — it
    is that no repo-creation call was even attempted."""
    import app.handlers.repos as repos
    _forbid(monkeypatch)

    # A scaffold that renders a secret — i.e. the description carries one.
    result = _create_project_repo(
        {"project": "Node Narrator", "name": "node-narrator",
         "description": "uses key sk-ant-" + "api03-AAAAbbbbCCCCddddEEEEffff"},
        ctx,
    )

    assert "won't commit" in result.lower() or "secret" in result.lower()
    ctx.db.refresh(project)
    assert project.repo_url == "", "no repo may be recorded when none was created"


def test_scan_block_is_logged_without_the_value(ctx, project, monkeypatch):
    _forbid(monkeypatch)
    secret = "sk-ant-" + "api03-SUPERSECRETVALUE0123456789abcdef"

    result = _create_project_repo(
        {"project": "Node Narrator", "name": "n", "description": f"key {secret}"}, ctx)

    assert secret not in result and "SUPERSECRETVALUE" not in result
    row = ctx.db.query(GithubWriteLog).filter_by(operation="create_repo").one()
    assert row.ok is False
    assert secret not in row.error and "SUPERSECRETVALUE" not in row.error
    assert "secret scan" in row.error


# ── 3. Public by default, BOTH paths ─────────────────────────────────────────
def test_create_project_repo_is_public_by_default(ctx, project, monkeypatch):
    sink = _install(monkeypatch)
    _create_project_repo({"project": "Node Narrator", "name": "node-narrator"}, ctx)
    assert sink["create_json"]["private"] is False


def test_create_project_repo_honors_explicit_private(ctx, project, monkeypatch):
    sink = _install(monkeypatch)
    _create_project_repo({"project": "Node Narrator", "name": "n", "visibility": "private"}, ctx)
    assert sink["create_json"]["private"] is True


def test_idea_promotion_is_public_by_default(ctx, db, monkeypatch):
    sink = _install(monkeypatch)
    idea = Idea(title="Node narrator", body="Read node status aloud.")
    db.add(idea)
    db.commit()

    _create_project_from_idea({"idea_id": idea.id, "project_name": "node-narrator"}, ctx)
    assert sink["create_json"]["private"] is False, "the ratified flip did not reach the idea path"


def test_idea_promotion_still_honors_explicit_private(ctx, db, monkeypatch):
    """The default flipped; the override did not go away."""
    sink = _install(monkeypatch)
    idea = Idea(title="Private thing", body="not for the world")
    db.add(idea)
    db.commit()

    _create_project_from_idea(
        {"idea_id": idea.id, "project_name": "p", "private": True}, ctx)
    assert sink["create_json"]["private"] is True


def test_the_readback_and_the_action_cannot_disagree(ctx, db, monkeypatch):
    """THE DRIFT THIS PR NEARLY SHIPPED. `_summarize_promote` applies its OWN
    `.get("private", ...)` default, so flipping only the handler would have made
    the gate say "private" while the code created "public" — the confirmation
    lying about an irreversible, outward-facing action.

    Asserted as an agreement between the two rather than as two separate string
    checks, because the failure mode is precisely that they drift apart.
    """
    sink = _install(monkeypatch)
    idea = Idea(title="X", body="y")
    db.add(idea)
    db.commit()

    args = {"idea_id": idea.id, "project_name": "x"}          # no `private` key
    readback = _summarize_promote(args)
    _create_project_from_idea(dict(args), ctx)

    said_private = "private" in readback
    did_private = sink["create_json"]["private"]
    assert said_private == did_private, (
        f"readback said {'private' if said_private else 'public'} but created "
        f"{'private' if did_private else 'public'}")
    assert "public" in readback


def test_idea_body_with_a_token_blocks_promotion(ctx, db, monkeypatch):
    """Idea bodies are free text from SMS and voice, committed verbatim — the
    likeliest place a pasted credential arrives. Under the old private default
    that was contained; public at the moment of commit it is not."""
    _forbid(monkeypatch)
    idea = Idea(title="Leaky", body="the key is sk-ant-" + "api03-AAAAbbbbCCCCddddEEEE")
    db.add(idea)
    db.commit()

    result = _create_project_from_idea({"idea_id": idea.id, "project_name": "leaky"}, ctx)

    assert "won't publish" in result.lower()
    db.refresh(idea)
    assert idea.promoted_url == "", "nothing may be marked promoted when nothing was created"


# ── 4. Idempotence (§6.2) ────────────────────────────────────────────────────
def test_existing_repo_is_adopted_not_overwritten(ctx, project, monkeypatch):
    """A half-created repo from a network failure must be recoverable by
    re-running — so an existing repo is reported and adopted, never re-seeded.
    Re-seeding would clobber real work in a repo somebody has already used."""
    sink = _install(monkeypatch, create_status=422, create_text="name already exists on this account")

    result = _create_project_repo({"project": "Node Narrator", "name": "existing-thing"}, ctx)

    assert "already exists" in result
    assert sink.get("puts") is None, "an existing repo must not be re-seeded"
    ctx.db.refresh(project)
    assert project.repo_url == "https://github.com/mdk32366/existing-thing"


def test_pregate_refuses_a_project_that_already_has_a_repo(ctx, project, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_x")
    project.repo_url = "https://github.com/mdk32366/already"
    ctx.db.commit()

    msg = _create_repo_pregate({"project": "Node Narrator", "name": "n"}, ctx)
    assert msg and "already has a repo" in msg


def test_pregate_refuses_without_a_name(ctx, project, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_x")
    assert "name" in (_create_repo_pregate({"project": "Node Narrator", "name": " "}, ctx) or "")


# ── 5. Happy path + records ──────────────────────────────────────────────────
def test_successful_creation_seeds_the_scaffold_and_records_repo_url(ctx, project, monkeypatch):
    sink = _install(monkeypatch)

    result = _create_project_repo(
        {"project": "Node Narrator", "name": "node-narrator"}, ctx)

    assert set(sink["puts"]) == {
        "README.md", "ARCHITECTURE.md", ".gitignore",
        "docs/README.md", "docs/archive/.gitkeep", "docs/operational/.gitkeep",
    }
    ctx.db.refresh(project)
    assert project.repo_url == "https://github.com/mdk32366/node-narrator"

    row = ctx.db.query(GithubWriteLog).filter_by(operation="create_repo").one()
    assert row.ok is True and row.error == ""
    assert "public" in result


def test_a_partial_seed_is_reported_as_partial(ctx, project, monkeypatch):
    """The §11.8 defect — the ideas path swallows failed seed PUTs and returns
    unqualified success — deliberately not repeated on the new path."""
    _install(monkeypatch, put_status=500)

    result = _create_project_repo({"project": "Node Narrator", "name": "n"}, ctx)

    assert "didn't land" in result
    row = ctx.db.query(GithubWriteLog).filter(
        GithubWriteLog.operation == "create_repo",
        GithubWriteLog.ok.is_(False)).one()
    assert "scaffold incomplete" in row.error


# ── 6. JARVIS creates; she does not flip ─────────────────────────────────────
def test_no_path_changes_an_existing_repos_visibility():
    """Go-private is owner action (ratified). Assert there is no code here that
    could flip an existing repo — not a convention, an absence."""
    import pathlib
    root = pathlib.Path(__file__).parent.parent / "app" / "handlers"
    for mod in ("repos.py", "ideas.py"):
        src = (root / mod).read_text(encoding="utf-8")
        assert ".patch(" not in src, f"{mod} can issue a PATCH — visibility could be flipped"
        assert "/topics" not in src
    # `private` is only ever sent on repo CREATION, never to an existing repo.
    src = (root / "repos.py").read_text(encoding="utf-8")
    assert src.count('"private": private') == 1
