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

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
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
        mark = "" if i.committed_sha else " (not yet committed)"
        lines.append(f"#{i.id}: {i.title}{mark}")
    return f"{len(rows)} recent idea(s):\n" + "\n".join(lines)


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
            "description": "List recently captured ideas.",
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "How many (default 10)."}},
            },
        },
        _list_ideas,
    )
