"""Durable job queue backed by the `jobs` table.

Long-running or best-effort work (e.g. the memory reflector) is enqueued and run
out-of-band by the worker process, so channel replies stay fast and work isn't
lost across restarts.

Handlers register with @job_handler("kind"). claim_next() atomically claims one
queued job; run_job() executes it with retry/backoff bookkeeping.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job

log = logging.getLogger(__name__)

# kind -> handler(db, payload: dict) -> str(result)
_HANDLERS: Dict[str, Callable[[Session, dict], str]] = {}


def job_handler(kind: str):
    def deco(fn: Callable[[Session, dict], str]):
        _HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(db: Session, kind: str, payload: Optional[dict] = None, *, channel: str = "",
            thread_key: str = "", actor: str = "", max_attempts: Optional[int] = None) -> Job:
    job = Job(
        kind=kind,
        payload=json.dumps(payload or {}),
        channel=channel,
        thread_key=thread_key,
        actor=actor,
        max_attempts=max_attempts or settings.job_max_attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next(db: Session, exclude_ids=None) -> Optional[Job]:
    """Atomically claim one queued job (lowest id first).

    On Postgres this uses ``FOR UPDATE SKIP LOCKED`` so multiple workers don't
    grab the same row; on SQLite the clause is simply omitted (single worker).
    ``exclude_ids`` lets a drain skip jobs it has already handled this pass, so a
    job that fails and requeues is retried on a *later* poll, not spun instantly.
    """
    is_pg = db.bind is not None and db.bind.dialect.name == "postgresql"
    q = select(Job).where(Job.status == "queued").order_by(Job.id)
    if exclude_ids:
        q = q.where(Job.id.not_in(set(exclude_ids)))
    if is_pg:
        q = q.with_for_update(skip_locked=True)
    job = db.execute(q.limit(1)).scalars().first()
    if job is None:
        db.commit()
        return None
    job.status = "running"
    job.attempts += 1
    db.commit()
    db.refresh(job)
    return job


def run_job(db: Session, job: Job) -> None:
    """Execute a claimed job, recording result or retry/error."""
    handler = _HANDLERS.get(job.kind)
    if handler is None:
        job.status = "error"
        job.error = f"No handler for kind '{job.kind}'"
        db.commit()
        return
    try:
        payload = json.loads(job.payload or "{}")
        result = handler(db, payload)
        job.status = "done"
        job.result = str(result)[:4000]
        job.error = ""
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        job = db.get(Job, job.id)
        job.error = str(e)[:4000]
        if job.attempts >= job.max_attempts:
            job.status = "error"
        else:
            job.status = "queued"  # retry on a later poll
        db.commit()
        log.error("job %s (%s) failed on attempt %d: %s", job.id, job.kind, job.attempts, e)


def process_available(db: Session, limit: int = 100) -> int:
    """Drain up to `limit` queued jobs once. Returns number processed.

    Each job is handled at most once per drain; jobs that fail and requeue are
    left for the next poll (avoids a hot retry loop without backoff)."""
    seen: set[int] = set()
    n = 0
    while n < limit:
        job = claim_next(db, exclude_ids=seen)
        if job is None:
            break
        seen.add(job.id)
        run_job(db, job)
        n += 1
    return n


# ── Built-in handlers ────────────────────────────────────────────────────────
@job_handler("email_copy")
def _handle_email_copy(db: Session, payload: dict) -> str:
    """Send an email copy of a reply (used to mirror SMS replies to the inbox)."""
    from app.notifier import send_email

    to = payload["to"]
    send_email(to, payload.get("subject", "JARVIS"), payload.get("body", ""))
    return f"emailed {to}"


@job_handler("morning_briefing")
def _handle_morning_briefing(db: Session, payload: dict) -> str:
    from app.briefing import send_briefing

    return send_briefing(db)


@job_handler("reflect")
def _handle_reflect(db: Session, payload: dict) -> str:
    from app.reflector import reflect_conversation

    convo_id = int(payload["conversation_id"])
    stored = reflect_conversation(db, convo_id)
    return f"stored {stored} fact(s)"


@job_handler("commit_idea")
def _handle_commit_idea(db: Session, payload: dict) -> str:
    """Push a captured idea to the ideas repo.

    Runs out-of-band on purpose: the Idea row is already committed to the DB
    before this is enqueued, so a bad token or a GitHub outage can delay the
    commit but can never lose the thought. Failure re-queues with backoff.
    """
    from app.handlers.ideas import commit_idea_to_repo

    return commit_idea_to_repo(db, int(payload["idea_id"]))
