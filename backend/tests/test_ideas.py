"""Ideas capability — the coverage that was missing.

The pre-existing tests only proved _capture_idea writes a DB row. The risky
half — the out-of-band GitHub commit (commit_idea_to_repo), _list_ideas, and
the capture path end-to-end through the orchestrator — had ZERO tests. This
file adds them, with the GitHub Contents API mocked (no network).
"""
import base64
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.handlers.base import Context
from app.handlers.ideas import _list_ideas, commit_idea_to_repo
from app.models import Idea, Job


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="sms", actor="+15551230000", thread_key="t1")


# ── A fake httpx client so commit_idea_to_repo makes no real network call ──────
class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Records the GET/PUT the commit makes and returns scripted responses."""
    def __init__(self, get_resp, put_resp, sink):
        self._get, self._put, self._sink = get_resp, put_resp, sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        self._sink["get_url"] = url
        return self._get

    def put(self, url, headers=None, json=None):
        self._sink["put_url"] = url
        self._sink["put_json"] = json
        return self._put


def _install_github(monkeypatch, get_resp, put_resp):
    monkeypatch.setattr(settings, "github_token", "ghp_test")
    monkeypatch.setattr(settings, "ideas_repo", "mdk32366/jarvis-ideas")
    monkeypatch.setattr(settings, "ideas_branch", "main")
    sink: dict = {}
    import httpx
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(get_resp, put_resp, sink))
    return sink


def _idea(db, title="Voice-first infra control", body="Read node status aloud.", tags="jarvis,infra"):
    row = Idea(title=title, body=body, tags=tags, source="sms")
    db.add(row)
    db.commit()
    db.refresh(row)
    row.created_at = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)  # deterministic path
    db.commit()
    return row


# ── commit_idea_to_repo: the untested GitHub push ─────────────────────────────
def test_commit_creates_a_new_markdown_file(db, monkeypatch):
    sink = _install_github(
        monkeypatch,
        get_resp=_Resp(404),                                   # path doesn't exist -> create
        put_resp=_Resp(201, {"content": {"sha": "newsha123"}}),
    )
    idea = _idea(db)

    result = commit_idea_to_repo(db, idea.id)

    assert "committed" in result
    # dated, slugged path under the ideas repo
    assert sink["put_url"].endswith("/ideas/2026/07/2026-07-17-voice-first-infra-control.md")
    payload = sink["put_json"]
    assert payload["branch"] == "main"
    assert payload["message"].startswith("idea:")
    assert "sha" not in payload, "a brand-new file must not send an update sha"
    # the committed markdown carries frontmatter + the user's own body
    content = base64.b64decode(payload["content"]).decode("utf-8")
    assert "title: Voice-first infra control" in content
    assert "source: sms" in content
    assert "tags: [jarvis, infra]" in content
    assert "Read node status aloud." in content
    # the sha is recorded so it isn't re-committed
    db.refresh(idea)
    assert idea.committed_sha == "newsha123"
    assert idea.commit_error == ""


def test_commit_updates_an_existing_file_with_its_blob_sha(db, monkeypatch):
    sink = _install_github(
        monkeypatch,
        get_resp=_Resp(200, {"sha": "oldsha"}),                # path exists -> update
        put_resp=_Resp(200, {"content": {"sha": "updatedsha"}}),
    )
    idea = _idea(db)

    commit_idea_to_repo(db, idea.id)

    assert sink["put_json"]["sha"] == "oldsha", "update must pass the existing blob sha"
    db.refresh(idea)
    assert idea.committed_sha == "updatedsha"


def test_commit_records_error_and_raises_for_retry_on_api_failure(db, monkeypatch):
    _install_github(monkeypatch, get_resp=_Resp(404), put_resp=_Resp(422, text="Unprocessable"))
    idea = _idea(db)

    with pytest.raises(RuntimeError):        # re-raised so the job queue retries
        commit_idea_to_repo(db, idea.id)

    db.refresh(idea)
    assert idea.committed_sha == ""          # not marked committed
    assert "422" in idea.commit_error


def test_commit_is_idempotent_and_guarded(db, monkeypatch):
    # already committed -> no-op
    idea = _idea(db)
    idea.committed_sha = "already"
    db.commit()
    assert "already committed" in commit_idea_to_repo(db, idea.id)

    # not configured -> no-op (never raises)
    monkeypatch.setattr(settings, "github_token", "")
    idea2 = _idea(db, title="Second")
    assert commit_idea_to_repo(db, idea2.id) == "ideas repo not configured"


# ── _list_ideas ───────────────────────────────────────────────────────────────
def test_list_ideas_empty(ctx, db):
    assert _list_ideas({}, ctx) == "No ideas captured yet."


def test_list_ideas_marks_uncommitted(ctx, db):
    committed = _idea(db, title="Committed one")
    committed.committed_sha = "abc"
    _idea(db, title="Fresh one")             # no sha
    db.commit()

    out = _list_ideas({}, ctx)
    assert "Committed one" in out and "Fresh one" in out
    assert "Fresh one (not yet committed)" in out
    assert "Committed one (not yet committed)" not in out


# ── End-to-end: capture through the orchestrator -> secretary -> capture_idea ──
def test_capture_idea_end_to_end_through_the_orchestrator(db, monkeypatch):
    """A user asking to note an idea flows orchestrator -> delegate(secretary) ->
    capture_idea, and the thought lands in the DB."""
    from app.orchestrator import run
    from fakes import install_llm, response, text_block, tool_block, ScriptedLLM

    llm = ScriptedLLM(
        # orchestrator hands it to the secretary
        response([tool_block("delegate",
                             {"agent": "secretary", "task": "capture the idea"}, id="d1")],
                 stop_reason="tool_use"),
        # secretary captures it
        response([tool_block("capture_idea",
                             {"body": "Let JARVIS narrate node status on the morning call.",
                              "tags": "jarvis"}, id="c1")], stop_reason="tool_use"),
        # secretary wraps up
        response([text_block("Captured that idea for you.")], stop_reason="end_turn"),
        # orchestrator's final reply
        response([text_block("Done — noted your idea.")], stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)

    reply = run(db, channel="sms", thread_key="+15551230000",
                user_text="I've got an idea — have JARVIS read node status on the morning call",
                actor="+15551230000")

    assert "noted" in reply.lower()
    idea = db.query(Idea).first()
    assert idea is not None
    assert "narrate node status" in idea.body
    assert idea.source == "sms"
