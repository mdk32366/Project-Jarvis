"""Background worker — drains the job queue on an interval.

Runs headless as the Fly `worker` process:
    python -m app.workers.job_worker --watch
    python -m app.workers.job_worker --once
"""

import argparse
import logging
import sys
import time

from app.config import settings
from app.database import SessionLocal
from app.jobs import process_available, recover_stale_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def run_once() -> int:
    db = SessionLocal()
    try:
        # Re-queue anything a dead/redeployed worker stranded in 'running' before
        # draining, so recovered jobs run this same pass (audit M2).
        recovered = recover_stale_jobs(db)
        if recovered:
            log.info("recovered %d stale job(s)", recovered)
        n = process_available(db)
        _place_due_calls(db)
        _check_watches(db)
        return n
    finally:
        db.close()


def _check_watches(db) -> None:
    """Run any due watches. This is what lets JARVIS act while nobody's asking.

    Never raises: a broken watch must not stop the job queue.
    """
    try:
        from app.handlers.watches import check_watch, due_watches

        for w in due_watches(db):
            check_watch(db, w)
    except Exception as e:  # noqa: BLE001
        log.error("watch loop error: %s", e)


def _place_due_calls(db) -> None:
    """Dial any queued outbound calls that are due.

    Runs on every worker tick (5s), so a callback the user asked for goes out
    within seconds rather than waiting for a job to be enqueued. Never raises:
    a dialling failure must not stop the job queue.
    """
    from app.runtime_settings import get_effective

    if not get_effective(db, "outbound_calls_enabled"):
        return
    try:
        from app.channels.outbound_voice import due_calls, place_call

        for row in due_calls(db):
            place_call(db, row)
    except Exception as e:  # noqa: BLE001
        log.error("outbound dial loop error: %s", e)


def _start_briefing_scheduler():
    """Schedule the daily morning briefing (enqueues a job the worker then runs).

    Reads the briefing time/enabled through the runtime overlay (`get_effective`)
    at bind time. A change made after start takes effect on the next worker
    restart; hot reschedule-on-change is R3 (scheduler hardening).
    """
    from app.runtime_settings import get_effective

    db0 = SessionLocal()
    try:
        if not get_effective(db0, "briefing_enabled"):
            log.info("morning briefing disabled (briefing_enabled=false)")
            return None
        brief_hour = get_effective(db0, "briefing_hour")
        brief_minute = get_effective(db0, "briefing_minute")
    finally:
        db0.close()
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        def _enqueue():
            db = SessionLocal()
            try:
                from app.jobs import enqueue

                # A call, not an alarm you have to set. That was the whole idea.
                kind = ("briefing_call" if get_effective(db, "briefing_by_phone")
                        and get_effective(db, "outbound_calls_enabled") else "morning_briefing")
                enqueue(db, kind, {}, channel="briefing", actor="scheduler")
                log.info("enqueued %s", kind)
            finally:
                db.close()

        sched = BackgroundScheduler(timezone=settings.calendar_timezone)
        sched.add_job(_enqueue, "cron", hour=brief_hour, minute=brief_minute)
        sched.start()
        log.info("briefing scheduled daily at %02d:%02d %s",
                 brief_hour, brief_minute, settings.calendar_timezone)
        return sched
    except Exception as e:  # noqa: BLE001
        log.error("could not start briefing scheduler: %s", e)
        return None


def watch(interval: int) -> None:
    log.info("Job worker watching every %ss", interval)
    _start_briefing_scheduler()
    while True:
        try:
            n = run_once()
            if n:
                log.info("processed %d job(s)", n)
        except Exception as e:  # noqa: BLE001
            log.error("worker loop error: %s", e)
        time.sleep(interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS job worker")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=settings.worker_poll_seconds)
    args = ap.parse_args(argv)
    if args.watch:
        watch(args.interval)
    else:
        print(f"processed {run_once()} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
