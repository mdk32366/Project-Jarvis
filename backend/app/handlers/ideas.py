"""Ideas handler — capture an idea, then commit it to a git repo.

Two-stage by design:

  1. `capture_idea` writes the Idea row and returns IMMEDIATELY. A network
     failure, a bad token, a GitHub outage — none of it can eat the thought.
  2. A `commit_idea` job (see app/jobs.py) pushes a markdown file to the
     jarvis-ideas repo out-of-band. `committed_sha` stays empty until it lands;
     `commit_error` records why if it doesn't.

The repo is separate from Project-Jarvis on purpose: idea churn stays out of the
code history, and the commit job never touches the app repo's CI.

Auth: a GitHub PAT with `repo` scope in GITHUB_TOKEN. Uses the Contents API
(PUT /repos/{owner}/{repo}/contents/{path}) — no git binary, no clone, no
working tree. One HTTP call per idea.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry, ToolFault
from app.models import Idea

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 20.0


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:60] or "idea").rstrip("-")


def _capture_idea(args: dict, ctx: Context) -> str:
    title = (args.get("title") or "").strip()
    body = (args.get("body") or "").strip()
    if not title and not body:
        return "Nothing to capture."
    if not title:
        # Derive a title rather than refusing — the thought matters more than the label.
        title = body.split(".")[0][:80]

    idea = Idea(
        title=title[:300],
        body=body[:20000],
        tags=(args.get("tags") or "")[:300],
        source=ctx.channel,
    )
    ctx.db.add(idea)
    ctx.db.commit()
    ctx.db.refresh(idea)

    # Commit out-of-band. Enqueue failure must not lose the idea — it's already
    # persisted above, and can be re-committed later.
    queued = ""
    if settings.github_token and settings.ideas_repo:
        try:
            from app.jobs import enqueue

            enqueue(ctx.db, "commit_idea", {"idea_id": idea.id},
                    channel=ctx.channel, thread_key=ctx.thread_key, actor=ctx.actor)
            queued = " Committing it to the ideas repo."
        except Exception as e:  # noqa: BLE001
            log.warning("could not enqueue commit_idea: %s", e)
    else:
        queued = " (Ideas repo not configured, so it's saved locally only.)"

    return f"Idea #{idea.id} captured: {idea.title}.{queued}"


def _list_ideas(args: dict, ctx: Context) -> str:
    q = select(Idea).order_by(Idea.created_at.desc()).limit(int(args.get("limit") or 10))
    rows = ctx.db.execute(q).scalars().all()
    if not rows:
        return "No ideas captured yet."
    lines = []
    for i in rows:
        if i.promoted_url:
            mark = f" (promoted → {i.promoted_url})"
        elif i.committed_sha:
            mark = ""
        else:
            mark = " (not yet committed)"
        lines.append(f"#{i.id}: {i.title}{mark}")
    return f"{len(rows)} recent idea(s):\n" + "\n".join(lines)


def _get_idea(args: dict, ctx: Context) -> str:
    """Read one captured idea in full, so it can be reviewed before promoting."""
    idea = ctx.db.get(Idea, int(args.get("idea_id") or 0))
    if idea is None:
        return f"No idea #{args.get('idea_id')}."
    tags = f"\nTags: {idea.tags}" if idea.tags else ""
    promoted = f"\nPromoted to: {idea.promoted_url}" if idea.promoted_url else ""
    return f"Idea #{idea.id}: {idea.title}{tags}{promoted}\n\n{idea.body or '(no detail captured)'}"


# ── create_project_from_idea (GATED, top-level) ──────────────────────────────
def _readme_md(project_name: str, idea: Idea) -> str:
    return "\n".join([
        f"# {project_name}",
        "",
        idea.body or "_(no detail captured)_",
        "",
        "---",
        f"_Seeded from JARVIS idea #{idea.id} ({idea.title}), "
        f"{(idea.created_at or datetime.utcnow()).strftime('%Y-%m-%d')}._",
        "",
    ])


def _idea_md(idea: Idea) -> str:
    tags = [t.strip() for t in (idea.tags or "").split(",") if t.strip()]
    return "\n".join([
        "---", f"title: {idea.title}",
        f"captured: {(idea.created_at or datetime.utcnow()).isoformat()}",
        f"source: {idea.source or 'unknown'}", f"tags: [{', '.join(tags)}]", "---", "",
        f"# {idea.title}", "", idea.body or "_(no detail captured)_", "",
    ])


def _explain_repo_error(status: int, text: str) -> str:
    low = (text or "").lower()
    if status == 422 and ("already exists" in low or "name already" in low):
        return ("A repo with that name already exists on the account — pick a different "
                "project name and I'll try again.")
    if status == 401:
        raise ToolFault("GitHub rejected the token. Check GITHUB_TOKEN (needs `repo` scope).")
    if status == 403:
        return "GitHub refused the request (permissions or rate limit). Try again shortly."
    return f"GitHub wouldn't create the repo ({status}): {text[:200]}"


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _promote_pregate(args: dict, ctx: Context) -> Optional[str]:
    """Refuse-outright checks BEFORE the confirmation gate (nothing to confirm)."""
    idea = ctx.db.get(Idea, int(args.get("idea_id") or 0))
    if idea is None:
        return (f"I don't have an idea #{args.get('idea_id')} to promote. "
                f"Say 'list my ideas' to see what's there.")
    if idea.promoted_url:
        return f"Idea #{idea.id} ('{idea.title}') is already a project: {idea.promoted_url}."
    if not settings.github_token:
        return "I can't create a GitHub repo — GITHUB_TOKEN isn't configured."
    if not (args.get("project_name") or "").strip():
        return "I need a name for the project repo — what should I call it?"
    return None


def _summarize_promote(args: dict) -> str:
    vis = "private" if args.get("private", True) else "public"
    name = (args.get("project_name") or "?").strip()
    return f"create a new {vis} GitHub repo '{name}' from idea #{args.get('idea_id')}"


def _create_project_from_idea(args: dict, ctx: Context) -> str:
    """GATED — runs only after the confirmation gate clears. Create a new GitHub
    repo from the idea, seed README.md + docs/idea.md, mark the idea promoted,
    and return the repo URL."""
    import httpx

    idea = ctx.db.get(Idea, int(args.get("idea_id") or 0))
    if idea is None:
        return f"No idea #{args.get('idea_id')}."
    if idea.promoted_url:                          # defensive: pregate already checks
        return f"Idea #{idea.id} is already a project: {idea.promoted_url}"
    if not settings.github_token:
        return "Can't create a repo — GITHUB_TOKEN isn't set."
    name = (args.get("project_name") or "").strip()
    if not name:
        return "I need a project name to create the repo."
    private = bool(args.get("private", True))
    description = (args.get("description") or f"Seeded from JARVIS idea #{idea.id}: {idea.title}")[:350]

    headers = _github_headers()
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(f"{_API}/user/repos", headers=headers,
                            json={"name": name, "private": private,
                                  "description": description, "auto_init": False})
            if r.status_code not in (200, 201):
                return _explain_repo_error(r.status_code, r.text)
            repo = r.json() or {}
            full = repo.get("full_name")
            html_url = repo.get("html_url", "")
            if not full:
                return "GitHub created something unexpected — no repo name came back."

            # Seed the repo. The first PUT creates the default branch.
            for path, content, msg in (
                ("README.md", _readme_md(name, idea), f"seed: {name}"),
                ("docs/idea.md", _idea_md(idea), "seed: original idea"),
            ):
                pr = client.put(
                    f"{_API}/repos/{full}/contents/{path}", headers=headers,
                    json={"message": msg,
                          "content": base64.b64encode(content.encode("utf-8")).decode("ascii")},
                )
                if pr.status_code not in (200, 201):
                    log.warning("seed PUT %s failed (%s): %s", path, pr.status_code, pr.text[:200])
    except ToolFault:
        raise  # a deliberate fault (e.g. 401) keeps its own message — don't rewrap
    except Exception as e:  # noqa: BLE001 — a tool must never crash the turn
        log.error("create_project_from_idea #%s failed: %s", idea.id, e)
        raise ToolFault(f"Couldn't create the project repo: {e}")

    idea.promoted_url = html_url
    ctx.db.commit()
    return f"Created the project repo: {html_url}"


# ── The commit job (registered in app/jobs.py) ───────────────────────────────
def commit_idea_to_repo(db, idea_id: int) -> str:
    """Push one idea to the ideas repo as markdown. Called by the job worker."""
    import httpx

    idea = db.get(Idea, idea_id)
    if idea is None:
        return f"no idea #{idea_id}"
    if idea.committed_sha:
        return f"idea #{idea_id} already committed"
    if not settings.github_token or not settings.ideas_repo:
        return "ideas repo not configured"

    when = (idea.created_at or datetime.utcnow())
    path = f"ideas/{when.strftime('%Y/%m')}/{when.strftime('%Y-%m-%d')}-{_slug(idea.title)}.md"

    tags = [t.strip() for t in (idea.tags or "").split(",") if t.strip()]
    front = [
        "---",
        f"title: {idea.title}",
        f"captured: {when.isoformat()}",
        f"source: {idea.source or 'unknown'}",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
        f"# {idea.title}",
        "",
        idea.body or "_(no detail captured)_",
        "",
    ]
    content = "\n".join(front)

    url = f"{_API}/repos/{settings.ideas_repo}/contents/{path}"
    payload = {
        "message": f"idea: {idea.title[:60]}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": settings.ideas_branch,
    }
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            # If the path already exists (same title, same day) GitHub needs the
            # blob sha to update rather than create. Fetch it; 404 means new.
            existing = client.get(url, headers=headers,
                                  params={"ref": settings.ideas_branch})
            if existing.status_code == 200:
                payload["sha"] = existing.json().get("sha")

            r = client.put(url, headers=headers, json=payload)
            if r.status_code not in (200, 201):
                raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
            sha = (r.json().get("content") or {}).get("sha", "")
    except Exception as e:  # noqa: BLE001
        idea.commit_error = str(e)[:2000]
        db.commit()
        log.error("commit_idea #%s failed: %s", idea_id, e)
        raise  # let the job queue retry with backoff

    idea.committed_sha = sha
    idea.commit_error = ""
    db.commit()
    return f"committed {path}"


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "capture_idea",
            "description": (
                "Capture an idea, thought, or concept the user wants to keep. Saves it "
                "immediately and commits it to their ideas repo. Use when they say they "
                "have an idea, want to note something down, or are thinking out loud "
                "about something worth keeping."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title for the idea."},
                    "body": {"type": "string",
                             "description": "The idea in full. Capture the user's own framing "
                                            "and reasoning, not just a summary."},
                    "tags": {"type": "string", "description": "Comma-separated tags."},
                },
                "required": ["body"],
            },
        },
        _capture_idea,
    )
    reg.register(
        {
            "name": "list_ideas",
            "description": "List recently captured ideas (shows which are committed or "
                           "already promoted to a project repo).",
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "How many (default 10)."}},
            },
        },
        _list_ideas,
    )
    reg.register(
        {
            "name": "get_idea",
            "description": "Read one captured idea in FULL (title, body, tags). Use before "
                           "promoting an idea to a project, or when the user asks to hear a "
                           "specific idea back.",
            "input_schema": {
                "type": "object",
                "properties": {"idea_id": {"type": "integer", "description": "Idea number from list_ideas."}},
                "required": ["idea_id"],
            },
        },
        _get_idea,
    )


def register_gated(reg: Registry) -> None:
    """Gated tools — top-level registry only (the confirmation gate runs in
    orchestrator.run; sub-agents refuse gated tools). Creating a named GitHub
    repo is irreversible and outward-facing, so it is gated like send_email."""
    reg.register(
        {
            "name": "create_project_from_idea",
            "description": (
                "Promote a captured idea into a NEW GitHub repository: creates the repo, "
                "seeds a README and the idea, and returns the link. IRREVERSIBLE — the "
                "system requires the user's explicit confirmation first. ASK the user for "
                "the project name if they didn't give one; never invent it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "idea_id": {"type": "integer", "description": "Which idea (from list_ideas)."},
                    "project_name": {"type": "string", "description": "Repo name the user chose."},
                    "private": {"type": "boolean", "description": "Default true (private repo)."},
                    "description": {"type": "string", "description": "Optional repo description."},
                },
                "required": ["idea_id", "project_name"],
            },
        },
        _create_project_from_idea,
        gated=True,                 # notional None -> always confirm
        summarize=_summarize_promote,
        pregate=_promote_pregate,
    )
