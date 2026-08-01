"""commit_document — branch + PR, and the scanner as a hard pre-write abort.

TDD #3 §9. PR #53 shipped the scanner with no caller; this file proves the
caller REFUSES. The sharpest assertion in the set is that on a scan hit the
GitHub client is never constructed at all — not that the function returned an
error, which a writer could satisfy while still having talked to GitHub first.

Fixture credentials are split across a `+` for the same reason as in
`test_secretscan.py`: GitHub push protection rejects contiguous token-shaped
literals, and joining them back up blocks the next push.
"""

import base64

import httpx
import pytest

from app.config import settings
from app.handlers.base import Context
from app.handlers.repos import _commit_document
from app.models import GithubWriteLog, Project, ProjectDocument


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture
def project(db):
    p = Project(name="JARVIS", summary="the assistant herself", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── A fake GitHub that records every call it is asked to make ────────────────
class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Scripted GitHub. Records URLs so the tests can assert what was *not*
    called as easily as what was."""

    def __init__(self, sink, put_status=201, pr_status=201):
        self.sink = sink
        self.put_status = put_status
        self.pr_status = pr_status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        self.sink["calls"].append(("GET", url))
        if "/git/ref/heads/" in url:
            return _Resp(200, {"object": {"sha": "basesha123"}})
        if "/contents/" in url:
            return _Resp(404)          # new file
        # Any bare /repos/<owner>/<name> — the repo-metadata read.
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
            if self.pr_status not in (200, 201):
                return _Resp(self.pr_status, {}, "nope")
            return _Resp(201, {"html_url": "https://github.com/mdk32366/Project-Jarvis/pull/99"})
        return _Resp(404)

    def put(self, url, headers=None, json=None):
        self.sink["calls"].append(("PUT", url))
        self.sink["put_url"] = url
        self.sink["put_json"] = json
        if self.put_status not in (200, 201):
            return _Resp(self.put_status, {}, "rejected")
        return _Resp(self.put_status, {"content": {"sha": "newsha"}})


def _install_github(monkeypatch, **kw):
    monkeypatch.setattr(settings, "github_token", "gh" + "p_testtoken")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    sink: dict = {"calls": []}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient(sink, **kw))
    return sink


def _forbid_github(monkeypatch):
    """Any attempt to construct a client is a test failure, not a soft signal."""
    monkeypatch.setattr(settings, "github_token", "gh" + "p_testtoken")
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")

    def _boom(*a, **k):
        raise AssertionError("GitHub client was constructed — the abort came too late")

    monkeypatch.setattr(httpx, "Client", _boom)


_CLEAN_DOC = """# TDD — Something

## 1. Problem

The brief narrates an absent section. See `app/briefing.py`.

```python
def f():
    return 1
```
"""


# ── 1. THE POINT OF THE PR: detection becomes refusal ────────────────────────
def test_scanner_blocks_the_commit_and_no_api_call_is_made(ctx, project, monkeypatch):
    """§9's sharpest line: assert the client was NEVER INVOKED.

    A writer that scans after opening a connection would pass a weaker test
    ("it returned an error") while having already talked to GitHub.
    """
    _forbid_github(monkeypatch)

    result = _commit_document(
        {"project": "JARVIS", "title": "Leaky design", "tier": "live",
         "body": "Here is the key: sk-ant-" + "api03-AAAAbbbbCCCCddddEEEEffff"},
        ctx,
    )

    assert "won't commit" in result.lower()
    assert ctx.db.query(ProjectDocument).count() == 0, "nothing may be recorded on a refusal"


def test_a_secret_in_the_title_also_blocks(ctx, project, monkeypatch):
    """A title is committed too. A credential in one is not less published for
    being short."""
    _forbid_github(monkeypatch)
    result = _commit_document(
        {"project": "JARVIS", "title": "key gh" + "p_ABCDEFGHIJ0123456789xyz",
         "tier": "live", "body": _CLEAN_DOC},
        ctx,
    )
    assert "won't commit" in result.lower()


def test_the_refusal_never_echoes_the_secret(ctx, project, monkeypatch):
    """The non-echo invariant at the ENFORCEMENT layer, where the value is now
    bound for a stored-and-rendered log. Mirrors #53's scanner-level test."""
    _forbid_github(monkeypatch)
    secret = "sk-ant-" + "api03-SUPERSECRETVALUE0123456789abcdef"

    result = _commit_document(
        {"project": "JARVIS", "title": "Leaky", "tier": "live",
         "body": f"key: {secret}"},
        ctx,
    )

    assert secret not in result
    assert "SUPERSECRETVALUE" not in result

    row = ctx.db.query(GithubWriteLog).filter_by(ok=False).one()
    assert secret not in row.error and "SUPERSECRETVALUE" not in row.error
    assert secret not in row.target
    assert "secret scan" in row.error and "anthropic_key" in row.error


# ── 2. Never main, never merge ───────────────────────────────────────────────
def test_commit_targets_a_branch_never_main(ctx, project, monkeypatch):
    sink = _install_github(monkeypatch)

    _commit_document({"project": "JARVIS", "title": "Some Design",
                      "tier": "live", "body": _CLEAN_DOC}, ctx)

    assert sink["ref"]["ref"].startswith("refs/heads/docs/some-design-")
    assert sink["put_json"]["branch"].startswith("docs/some-design-")
    assert sink["put_json"]["branch"] != "main"
    assert sink["pr"]["head"].startswith("docs/some-design-")
    assert sink["pr"]["base"] == "main", "the PR targets the default branch"


def test_never_merges(ctx, project, monkeypatch):
    """Asserted, not trusted (§9). 'Never merges' is exactly the invariant that
    erodes the first time somebody wants it to be convenient."""
    sink = _install_github(monkeypatch)

    _commit_document({"project": "JARVIS", "title": "Some Design",
                      "tier": "live", "body": _CLEAN_DOC}, ctx)

    for method, url in sink["calls"]:
        assert "/merge" not in url, f"a merge endpoint was called: {method} {url}"

    # And no merge call exists in the source at all.
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "app" / "handlers" / "repos.py").read_text(
        encoding="utf-8")
    assert "/merge" not in src


# ── 3. Tier is the path, and the caller cannot override it ───────────────────
@pytest.mark.parametrize("tier, prefix", [
    ("live", "docs/"),
    ("archive", "docs/archive/"),
    ("operational", "docs/operational/"),
])
def test_tier_resolves_the_path(ctx, project, monkeypatch, tier, prefix):
    sink = _install_github(monkeypatch)
    _commit_document({"project": "JARVIS", "title": "Tier Doc",
                      "tier": tier, "body": _CLEAN_DOC}, ctx)
    assert sink["put_url"].endswith(f"/contents/{prefix}tier-doc.md")


def test_caller_supplied_path_is_ignored(ctx, project, monkeypatch):
    """The convention-enforcement point. A caller that could supply a path could
    write an archive document into `docs/`."""
    sink = _install_github(monkeypatch)
    _commit_document({"project": "JARVIS", "title": "Tier Doc", "tier": "archive",
                      "path": "docs/somewhere-else.md", "body": _CLEAN_DOC}, ctx)
    assert sink["put_url"].endswith("/contents/docs/archive/tier-doc.md")
    assert "somewhere-else" not in sink["put_url"]


def test_the_schema_has_no_path_argument(ctx):
    """Stronger than ignoring it: the tool does not offer one."""
    from app.handlers.repos import _SCHEMA
    assert "path" not in _SCHEMA["input_schema"]["properties"]


# ── 4. Never guess a repo ────────────────────────────────────────────────────
def test_unresolvable_repo_aborts_with_no_api_call(ctx, db, monkeypatch):
    _forbid_github(monkeypatch)
    p = Project(name="Some Other Thing", status="active", repo_url="")
    db.add(p)
    db.commit()

    result = _commit_document({"project": "Some Other Thing", "title": "X",
                               "tier": "live", "body": _CLEAN_DOC}, ctx)

    assert "no repo recorded" in result
    assert db.query(ProjectDocument).count() == 0


def test_project_repo_url_is_translated_to_owner_name(ctx, db, monkeypatch):
    sink = _install_github(monkeypatch)
    monkeypatch.setattr(settings, "jarvis_repo", "mdk32366/Project-Jarvis")
    p = Project(name="Node Narrator", status="active",
                repo_url="https://github.com/mdk32366/node-narrator")
    db.add(p)
    db.commit()

    _commit_document({"project": "Node Narrator", "title": "Plan",
                      "tier": "live", "body": _CLEAN_DOC}, ctx)

    assert any("/repos/mdk32366/node-narrator" in url for _, url in sink["calls"])


# ── 5. Happy path ────────────────────────────────────────────────────────────
def test_clean_document_commits_opens_pr_and_records_everything(ctx, project, monkeypatch):
    sink = _install_github(monkeypatch)

    result = _commit_document({"project": "JARVIS", "title": "Some Design",
                               "tier": "live", "kind": "tdd", "body": _CLEAN_DOC}, ctx)

    # The document went up verbatim.
    assert base64.b64decode(sink["put_json"]["content"]).decode("utf-8") == _CLEAN_DOC

    # attach_document was REUSED — a real ProjectDocument row, not a second insert path.
    doc = ctx.db.query(ProjectDocument).one()
    assert doc.title == "Some Design"
    assert doc.tier == "live" and doc.kind == "tdd"
    assert doc.path == "docs/some-design.md"
    assert doc.url.endswith("/pull/99")

    # Both write-log rows, both ok.
    ops = {r.operation: r for r in ctx.db.query(GithubWriteLog).all()}
    assert set(ops) == {"commit_doc", "open_pr"}
    assert all(r.ok for r in ops.values())
    assert all(r.error == "" for r in ops.values())
    assert ops["commit_doc"].ref.startswith("docs/some-design-")

    assert "pull/99" in result and "Nothing merged" in result


# ── 6. Failures are recorded, not swallowed ──────────────────────────────────
def test_write_log_records_a_failed_commit(ctx, project, monkeypatch):
    """The §11.8 lesson applied to the new path: a failed write must not report
    success, and must leave a diagnosable row."""
    from app.handlers.base import ToolFault
    _install_github(monkeypatch, put_status=422)

    with pytest.raises(ToolFault):
        _commit_document({"project": "JARVIS", "title": "Some Design",
                          "tier": "live", "body": _CLEAN_DOC}, ctx)

    row = ctx.db.query(GithubWriteLog).filter_by(operation="commit_doc").one()
    assert row.ok is False and "422" in row.error
    assert ctx.db.query(ProjectDocument).count() == 0, "no orphaned document row"


def test_a_failed_pr_still_reports_the_committed_branch(ctx, project, monkeypatch):
    """Partial success is reported AS partial — the defect §11.8 names is
    exactly a partial write reporting unqualified success."""
    _install_github(monkeypatch, pr_status=500)

    result = _commit_document({"project": "JARVIS", "title": "Some Design",
                               "tier": "live", "body": _CLEAN_DOC}, ctx)

    assert "couldn't open the PR" in result
    assert "safe on the branch" in result
    rows = {r.operation: r for r in ctx.db.query(GithubWriteLog).all()}
    assert rows["commit_doc"].ok is True
    assert rows["open_pr"].ok is False


# ── 7. Reachability: registered, audited, and NOT on voice ───────────────────
def test_commit_document_is_registered_and_ungated():
    from app.handlers.base import build_registry
    reg = build_registry(include_delegate=True)
    assert reg.has("commit_document")
    assert not reg.is_gated("commit_document"), "a branch + PR is reversible"


def test_commit_document_is_not_reachable_from_voice():
    """STRUCTURAL, not conventional. Voice auth is caller-ID and spoofable, so a
    tool that writes to a repository must not be on that channel — and the
    exclusion is enforced by `restrict`, which is what makes it fail closed."""
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
    from app.handlers.base import build_registry

    assert "commit_document" not in VOICE_TOOLS_PHASE1
    voice_reg = build_registry(include_delegate=True, allow=VOICE_TOOLS_PHASE1)
    assert not voice_reg.has("commit_document")


def test_commit_document_is_on_no_agent_roster():
    """The other half of the voice exclusion: every one of the nine agents is
    voice-reachable, so a roster entry would drag it onto the phone."""
    from app.agents import DEFAULT_AGENTS
    for name, agent in DEFAULT_AGENTS.items():
        assert "commit_document" not in agent.tools, f"{name} would expose it to voice"


def test_it_runs_through_the_registry_seam(ctx, project, monkeypatch):
    """The audit-starvation lesson: exercised through `run_tool`, so it lands in
    `actions_audit` like everything else rather than becoming a latent latch."""
    from app.handlers.base import build_registry
    _install_github(monkeypatch)

    reg = build_registry(include_delegate=True)
    result, status = reg.run_tool("commit_document", {
        "project": "JARVIS", "title": "Via Registry", "tier": "live", "body": _CLEAN_DOC}, ctx)

    assert status == "ok"
    assert "pull/99" in result


def test_a_scan_refusal_is_not_an_audit_fault(ctx, project, monkeypatch):
    """A refusal is the tool WORKING, so it must not read as a fault — the same
    reasoning that keeps confirmed/refused gate outcomes in the ok-family. A
    scanner hit reading `error` would teach the health substrate that a
    correctly-defended write is a broken component."""
    from app.handlers.base import build_registry
    _forbid_github(monkeypatch)

    reg = build_registry(include_delegate=True)
    _, status = reg.run_tool("commit_document", {
        "project": "JARVIS", "title": "Leaky", "tier": "live",
        "body": "key: sk-ant-" + "api03-AAAAbbbbCCCCddddEEEEffff"}, ctx)

    assert status == "ok", "a refusal is not a fault"
