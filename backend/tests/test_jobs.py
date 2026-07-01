from app import jobs
from app.jobs import claim_next, enqueue, job_handler, process_available, run_job
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
