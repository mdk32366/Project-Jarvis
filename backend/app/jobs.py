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

# Jobs whose failure must NOT trigger a failure-notification — the notification
# is itself an email_copy job, so notifying about a failed email would recurse
# forever, spawning jobs faster than the worker can drain them.
# distill_episode joins reflect here: both are best-effort memory jobs that run
# after every conversation — a transient LLM failure emailing the owner each
# time would be pure noise. Failures still land in the job row and the log.
_NEVER_NOTIFY = {"email_copy", "reflect", "distill_episode"}


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

        # Some failures will NEVER succeed on retry — a disabled Google API, a
        # revoked token. Burning three attempts on those just delays the honest
        # answer and buries it deeper in the log.
        from app.google_oauth import is_permanent

        permanent = is_permanent(e)
        if permanent or job.attempts >= job.max_attempts:
            job.status = "error"
            _notify_job_failure(db, job, e)
        else:
            job.status = "queued"  # retry on a later poll
        db.commit()
        log.error("job %s (%s) failed on attempt %d%s: %s",
                  job.id, job.kind, job.attempts,
                  " (permanent)" if permanent else "", e)


def _notify_job_failure(db: Session, job: Job, err: Exception) -> None:
    """Tell the user a background job died.

    THIS IS THE REAL LESSON from the first live run. `sync_contacts` and
    `push_task` failed with a perfectly clear Google error — "the People API
    isn't enabled in your project" — three times each, and then died. JARVIS said
    nothing. The user only discovered it by hand-querying Postgres.

    A silent background failure is barely better than no feature at all: the user
    believes the thing worked. So when a job dies, say so — and say WHAT TO DO,
    because Google's errors are actually actionable if you actually read them.
    """
    from app.google_oauth import explain

    if not settings.owner_email_resolved:
        return

    # DO NOT notify about a failed notification. _notify_job_failure enqueues an
    # email_copy job; if THAT fails, notifying again enqueues another, which fails,
    # which notifies... an unbounded loop that generates jobs faster than the
    # worker drains them. Caught by the test suite hanging; it would have been far
    # nastier in production, where the "failing" job is a real SMTP outage.
    if job.kind in _NEVER_NOTIFY:
        log.warning("job %s (%s) failed; not notifying (would recurse)", job.id, job.kind)
        return

    hint = explain(err)
    try:
        body = [f"A background task failed and won't be retried.", "",
                f"Task: {job.kind}"]
        if hint:
            body += ["", "What's wrong:", hint]
        body += ["", "Details:", str(err)[:1500]]
        enqueue(
            db, "email_copy",
            {"to": settings.owner_email_resolved,
             "subject": f"JARVIS: {job.kind} failed",
             "body": "\n".join(body)},
            channel="system", actor="system",
        )
    except Exception as e2:  # noqa: BLE001 — never let the notifier break the worker
        log.warning("could not notify about failed job %s: %s", job.id, e2)


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


@job_handler("distill_episode")
def _handle_distill_episode(db: Session, payload: dict) -> str:
    """Distill a closed conversation into an episodic memory (TDD #14).

    A job for the same reason reflect is: it makes an LLM call, which must
    never block a hangup or a reply."""
    from app.episodic import distill_episode

    return distill_episode(
        db,
        channel=payload["channel"],
        thread_key=payload["thread_key"],
        source_ref=payload.get("source_ref", ""),
    )


@job_handler("commit_idea")
def _handle_commit_idea(db: Session, payload: dict) -> str:
    """Push a captured idea to the ideas repo.

    Runs out-of-band on purpose: the Idea row is already committed to the DB
    before this is enqueued, so a bad token or a GitHub outage can delay the
    commit but can never lose the thought. Failure re-queues with backoff.
    """
    from app.handlers.ideas import commit_idea_to_repo

    return commit_idea_to_repo(db, int(payload["idea_id"]))


@job_handler("sync_contacts")
def _handle_sync_contacts(db: Session, payload: dict) -> str:
    """Import Google Contacts. Out-of-band: a full address book is several
    paginated API calls, far too slow to sit inside a phone call."""
    from app.handlers.contacts import sync_google_contacts

    return sync_google_contacts(db)


@job_handler("push_task")
def _handle_push_task(db: Session, payload: dict) -> str:
    """Push a task to Google Tasks so it appears on the user's phone."""
    from app.handlers.tasks import push_task_to_google

    return push_task_to_google(db, int(payload["task_id"]))


@job_handler("complete_task_google")
def _handle_complete_task_google(db: Session, payload: dict) -> str:
    from app.handlers.tasks import complete_task_in_google

    return complete_task_in_google(db, int(payload["task_id"]))


@job_handler("briefing_call")
def _handle_briefing_call(db: Session, payload: dict) -> str:
    """The morning brief, as a CALL rather than an alarm you have to set.

    The brief is composed FIRST and stored as the call's opening line, so there
    is no dead air while an LLM thinks after you pick up. If composing fails we
    simply don't ring — better silence than a call with nothing to say.
    """
    from app.briefing import compose_briefing
    from app.channels.outbound_voice import schedule_call

    text = compose_briefing(db)
    if not text or text.startswith("(no briefing"):
        return "nothing to brief"

    opening = f"Good morning. Here's your brief. {text}"
    row = schedule_call(db, opening=opening, kind="briefing",
                        context="Morning briefing. The user may ask follow-ups.")
    return f"queued briefing call #{row.id}" if row else "could not queue briefing call"


@job_handler("place_calls")
def _handle_place_calls(db: Session, payload: dict) -> str:
    """Dial anything queued and due. Runs on the worker's tick."""
    from app.channels.outbound_voice import due_calls, place_call

    if not settings.outbound_calls_enabled:
        return "outbound disabled"

    rows = due_calls(db)
    if not rows:
        return "nothing due"
    results = [place_call(db, r) for r in rows]
    return f"placed {len(results)}: " + "; ".join(results)
