from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import jobs
from app.jobs import claim_next, enqueue, job_handler, process_available, recover_stale_jobs, run_job
from app.models import Job

# a throwaway handler for tests
_seen = []


@job_handler("test_echo")
def _echo(db, payload):
    _seen.append(payload.get("v"))
    return f"echoed {payload.get('v')}"


def test_enqueue_and_claim(db):
    enqueue(db, "test_echo", {"v": 1})
    job = claim_next(db)
    assert job is not None and job.status == "running" and job.attempts == 1
    assert claim_next(db) is None  # nothing else queued


def test_run_job_success(db):
    _seen.clear()
    enqueue(db, "test_echo", {"v": 42})
    n = process_available(db)
    assert n == 1
    assert _seen == [42]
    j = db.query(Job).first()
    assert j.status == "done" and "echoed 42" in j.result


def test_unknown_kind_errors(db):
    enqueue(db, "no_such_kind", {})
    process_available(db)
    j = db.query(Job).first()
    assert j.status == "error" and "No handler" in j.error


def test_retry_then_fail(db, monkeypatch):
    calls = {"n": 0}

    @job_handler("always_boom")
    def _boom(db, payload):
        calls["n"] += 1
        raise RuntimeError("boom")

    enqueue(db, "always_boom", {}, max_attempts=2)
    process_available(db)  # attempt 1 -> requeued
    j = db.query(Job).first()
    assert j.status == "queued" and j.attempts == 1
    process_available(db)  # attempt 2 -> error (max reached)
    j = db.query(Job).first()
    assert j.status == "error" and j.attempts == 2
    assert calls["n"] == 2


# ── M2: orphaned-job recovery ────────────────────────────────────────────────
def _running_job(db, *, attempts=1, max_attempts=3, age_seconds=0) -> Job:
    """A job in 'running' whose updated_at is `age_seconds` in the past. The raw
    UPDATE backdates updated_at without tripping the onupdate=now() the ORM would."""
    j = Job(kind="test_echo", payload="{}", status="running",
            attempts=attempts, max_attempts=max_attempts)
    db.add(j); db.commit(); db.refresh(j)
    old = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).replace(tzinfo=None)
    db.execute(text("UPDATE jobs SET updated_at = :t WHERE id = :i"), {"t": old, "i": j.id})
    db.commit(); db.refresh(j)
    return j


def test_recover_requeues_stale_running_job(db):
    j = _running_job(db, age_seconds=10_000)
    assert recover_stale_jobs(db, stale_seconds=300) == 1
    db.refresh(j)
    assert j.status == "queued"   # will be retried instead of lost


def test_recover_leaves_a_freshly_claimed_job_alone(db):
    j = _running_job(db, age_seconds=0)     # claimed just now — may be running
    assert recover_stale_jobs(db, stale_seconds=300) == 0
    db.refresh(j)
    assert j.status == "running"


def test_recover_fails_stale_job_past_max_attempts(db):
    j = _running_job(db, attempts=3, max_attempts=3, age_seconds=10_000)
    assert recover_stale_jobs(db, stale_seconds=300) == 1
    db.refresh(j)
    assert j.status == "error"    # don't loop a poison job forever
