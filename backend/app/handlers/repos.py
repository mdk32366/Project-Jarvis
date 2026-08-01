"""Document commits to GitHub — branch + PR, never `main`.

TDD #3 (`docs/TDD-repo-scaffolding.md` §4.1, §4.5, §6.1). This is where the
secret scanner shipped in PR #53 stops being detection and becomes **refusal**:
`scan_for_secrets` runs before any GitHub client is constructed, and a finding
aborts the write. The scanner and this writer can never again be separated —
that separation is what the build order sequenced deliberately (§8), and it
became a safety property rather than a preference when public-by-default repo
visibility was ratified (§11.3).

WHY UNGATED. A branch and a PR are reversible: nothing reaches `main` without a
human merging it, and the confirmation gate is for actions you cannot take back.
Diluting the gate with reversible work is how a gate stops being read. Repo
*creation* is the irreversible half and stays gated (§4.1) — it is not in this
module yet.

WHY NOT VOICE-REACHABLE. Voice auth is caller-ID, which is spoofable
(`channels/voice_pipeline.py`). A tool that writes to a repository has no
business on that channel. The exclusion is STRUCTURAL, not a convention: this
tool is registered top-level, and `orchestrator._run_inner` restricts the
top-level registry to `VOICE_TOOLS_PHASE1` on a voice call, so absence from that
allowlist removes it. Registering it on a sub-agent roster instead would have
made it voice-reachable — every one of the nine agents is in
`VOICE_AGENTS_PHASE1`, and a roster must be a subset of the voice allowlist.
Asserted in test rather than trusted.

THE KEY DIFFERENCE FROM THE IDEAS PATH. `ideas.commit_idea_to_repo` PUTs
straight at a branch with no PR. This is branch **+ PR**, and there is no merge
call anywhere on this path — also asserted in test (§9), because "never merges"
is the kind of invariant that erodes the first time somebody wants it to be
convenient.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.handlers.base import Context, Registry, ToolFault
from app.handlers.ideas import _slug
from app.handlers.projects import DOC_TIERS, _attach_document, _find_project
from app.models import GithubWriteLog
from app.secretscan import SecretFinding, scan_for_secrets

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 20.0

# The convention-enforcement point (§6.1 step 3). The caller supplies a TIER and
# the function derives the path; there is deliberately no `path` argument on the
# schema. A caller that could supply a path could write an archive document into
# `docs/`, which is exactly the ambiguity the tier convention exists to kill.
TIER_PATHS: dict[str, str] = {
    "live": "docs/",
    "archive": "docs/archive/",
    "operational": "docs/operational/",
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _log_write(db, *, operation: str, target: str, ref: str = "",
               ok: bool = False, error: str = "") -> None:
    """Record one attempted GitHub write.

    Never raises: failing to record a write must not fail the write, the same
    contract `record_tool_audit` holds. Written on its own commit so a caller
    mid-transaction does not have its work committed early by a log write.

    `error` NEVER carries a scanner finding's matched value — see
    `_refusal_message`. This row is stored AND rendered on the status page, so a
    leak here leaks twice.
    """
    try:
        db.add(GithubWriteLog(
            operation=operation, target=target[:400], ref=ref[:400],
            ok=ok, error=error[:2000],
        ))
        db.commit()
    except Exception:  # noqa: BLE001 — recording must never break the caller
        db.rollback()
        log.warning("could not record github_write_log row for %r", operation)


def _refusal_message(findings: list[SecretFinding]) -> str:
    """Render a refusal from pattern name + location ONLY.

    `SecretFinding` has no value-bearing field at all (PR #53), so this cannot
    leak the matched substring even by accident — the invariant is enforced by
    the shape of the data, not by this function remembering to be careful. That
    is the stronger form and it is why the finding was designed that way.
    """
    where = "; ".join(f"{f.pattern_name} on line {f.line}" for f in findings[:5])
    more = f" (+{len(findings) - 5} more)" if len(findings) > 5 else ""
    return (
        f"I won't commit that — it looks like it contains a secret: {where}{more}. "
        f"Nothing was sent to GitHub. Remove the credential and ask me again."
    )


# Project names that mean "JARVIS herself" and route to her own repo. A name
# match rather than a flag because the alternative — an `is_jarvis` column — is a
# schema change to encode one row.
_JARVIS_PROJECT_NAMES = {"jarvis", "project jarvis", "project-jarvis"}


def _resolve_repo(project) -> tuple[str, Optional[str]]:
    """(repo_full_name, error). Never guesses a repo (§6.1 step 2).

    `project.repo_url` is a browser URL; the API needs `owner/name`.
    """
    if project.name.strip().lower() in _JARVIS_PROJECT_NAMES:
        if not settings.jarvis_repo:
            return "", "JARVIS_REPO isn't configured, so I don't know where to commit that."
        return settings.jarvis_repo, None

    url = (project.repo_url or "").strip().rstrip("/")
    if not url:
        return "", (
            f"{project.name} has no repo recorded, so there's nowhere to commit that. "
            f"Create the project repo first, or set its repo URL."
        )
    if "github.com/" not in url:
        return "", f"{project.name}'s repo URL isn't a GitHub URL: {url}"
    full = url.split("github.com/", 1)[1]
    if full.count("/") != 1 or not all(full.split("/")):
        return "", f"I can't work out the repo from {url}."
    return full, None


def _commit_document(args: dict, ctx: Context) -> str:
    """Commit a document to its project's repo on a branch, and open a PR.

    The order of operations is load-bearing (§6.1) and the scan is first: no
    GitHub client is constructed until the text is clean.
    """
    import httpx

    p, err = _find_project(ctx.db, args.get("project"))
    if err:
        return err

    title = (args.get("title") or "").strip()
    if not title:
        return "A document needs a title."
    body = args.get("body") or ""
    if not body.strip():
        return "There's nothing in that document to commit."

    tier = (args.get("tier") or "live").strip().lower()
    if tier not in DOC_TIERS:
        return f"Tier must be one of: {', '.join(DOC_TIERS)}."
    kind = (args.get("kind") or "other").strip().lower()[:32]

    # ── 1. SCAN FIRST (§4.5). Detection becomes refusal here. ────────────────
    # Title as well as body: a title is committed too, and a credential pasted
    # into one is not less published for being short.
    findings = scan_for_secrets(body) + scan_for_secrets(title)
    if findings:
        names = ",".join(sorted({f.pattern_name for f in findings}))
        _log_write(ctx.db, operation="commit_doc", target=f"{p.name}:{title}",
                   ok=False, error=f"blocked by secret scan: {names}")
        return _refusal_message(findings)

    # ── 2. Resolve the destination. Never guess. ─────────────────────────────
    repo, err = _resolve_repo(p)
    if err:
        _log_write(ctx.db, operation="commit_doc", target=p.name, ok=False, error=err)
        return err
    if not settings.github_token:
        return "I can't commit that — GITHUB_TOKEN isn't configured."

    # ── 3. Path from TIER, never from the caller. ────────────────────────────
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = _slug(title)
    path = f"{TIER_PATHS[tier]}{slug}.md"
    branch = f"docs/{slug}-{stamp}"

    headers = _headers()
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            # Default branch — read it, don't assume "main". A wrong base makes
            # a PR that silently diffs against the wrong tree.
            r = client.get(f"{_API}/repos/{repo}", headers=headers)
            if r.status_code == 401:
                raise ToolFault("GitHub rejected the token. Check GITHUB_TOKEN (needs `repo` scope).")
            if r.status_code != 200:
                raise ToolFault(f"Couldn't read {repo} ({r.status_code}).")
            base = (r.json() or {}).get("default_branch") or "main"

            # Branch off the base ref.
            r = client.get(f"{_API}/repos/{repo}/git/ref/heads/{base}", headers=headers)
            if r.status_code != 200:
                raise ToolFault(f"Couldn't read {repo}'s {base} branch ({r.status_code}).")
            base_sha = ((r.json() or {}).get("object") or {}).get("sha", "")

            r = client.post(f"{_API}/repos/{repo}/git/refs", headers=headers,
                            json={"ref": f"refs/heads/{branch}", "sha": base_sha})
            # 422 = the ref already exists. Re-running a commit after a failure
            # part-way through must not be destructive (§6.2's idempotence
            # reasoning, applied here).
            if r.status_code not in (200, 201, 422):
                raise ToolFault(f"Couldn't create branch {branch} ({r.status_code}).")

            # Commit onto the branch. Fetch the blob sha first so a re-run
            # updates rather than 409s — the ideas path's pattern.
            contents_url = f"{_API}/repos/{repo}/contents/{path}"
            payload = {
                "message": f"docs: {title[:60]}",
                "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            existing = client.get(contents_url, headers=headers, params={"ref": branch})
            if existing.status_code == 200:
                payload["sha"] = (existing.json() or {}).get("sha")

            r = client.put(contents_url, headers=headers, json=payload)
            if r.status_code not in (200, 201):
                raise ToolFault(f"Couldn't commit {path} ({r.status_code}): {r.text[:200]}")
            _log_write(ctx.db, operation="commit_doc", target=f"{repo}:{path}",
                       ref=branch, ok=True)

            # Open the PR. There is NO merge call here and there must never be
            # one — JARVIS writes designs and leaves the decision to a human.
            r = client.post(f"{_API}/repos/{repo}/pulls", headers=headers,
                            json={"title": f"docs: {title[:60]}", "head": branch,
                                  "base": base,
                                  "body": f"Document committed by JARVIS.\n\nTier: {tier}\nKind: {kind}"})
            if r.status_code not in (200, 201):
                _log_write(ctx.db, operation="open_pr", target=repo, ref=branch, ok=False,
                           error=f"{r.status_code}: {r.text[:200]}")
                return (f"Committed {path} to branch {branch}, but couldn't open the PR "
                        f"({r.status_code}). The work is safe on the branch.")
            pr_url = (r.json() or {}).get("html_url", "")
            _log_write(ctx.db, operation="open_pr", target=repo, ref=pr_url or branch, ok=True)
    except ToolFault as e:
        _log_write(ctx.db, operation="commit_doc", target=f"{repo}:{path}",
                   ref=branch, ok=False, error=str(e))
        raise
    except Exception as e:  # noqa: BLE001 — a tool must never crash the turn
        _log_write(ctx.db, operation="commit_doc", target=f"{repo}:{path}",
                   ref=branch, ok=False, error=str(e)[:2000])
        log.error("commit_document to %s failed: %s", repo, e)
        raise ToolFault(f"Couldn't commit the document: {e}")

    # ── 4. Record it against the project. REUSES attach_document — a second
    # ProjectDocument insert would be a second place for the tracker and the
    # repo to disagree, which is the thing `project_document` exists to prevent.
    _attach_document({"project": p.name, "kind": kind, "tier": tier,
                      "title": title, "path": path, "url": pr_url}, ctx)

    return (f"Committed '{title}' to {repo} as {path} on branch {branch}, "
            f"and opened a PR: {pr_url}. Nothing merged — that's yours to review.")


_SCHEMA = {
    "name": "commit_document",
    "description": (
        "Commit a design document to a project's GitHub repo on a NEW BRANCH and open "
        "a pull request. Never commits to main and never merges. The destination path "
        "is derived from the tier, so pass the tier, not a path. Refuses outright if "
        "the document appears to contain a credential."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name or id. Use 'JARVIS' for her own repo."},
            "title": {"type": "string", "description": "Document title — also the filename slug."},
            "body": {"type": "string", "description": "The full markdown document."},
            "tier": {"type": "string", "enum": list(DOC_TIERS),
                     "description": "live (current design), archive (superseded), operational (executed handoff)."},
            "kind": {"type": "string", "description": "tdd, test-plan, ui-plan, closeout, readme, other"},
        },
        "required": ["project", "title", "body"],
    },
}


def register(reg: Registry) -> None:
    """Ungated, TOP-LEVEL registration — and the placement is the security
    control, not a filing decision.

    Top-level means `orchestrator._run_inner`'s voice branch
    (`build_registry(..., allow=VOICE_TOOLS_PHASE1)`) removes it on a phone
    call, because it is absent from that allowlist. Putting it on a sub-agent
    roster instead would make it voice-reachable: all nine agents are in
    `VOICE_AGENTS_PHASE1`, and a roster is required to be a subset of the voice
    allowlist, so a roster entry would have to be added to the allowlist too.
    Fail-closed by construction.
    """
    reg.register(_SCHEMA, _commit_document)
