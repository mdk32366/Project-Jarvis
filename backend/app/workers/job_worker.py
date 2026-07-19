"""Background worker — drains the job queue on an interval.

Runs headless as the Fly `worker` process:
    python -m app.workers.job_worker --watch
    python -m app.workers.job_worker --once
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
        _briefing_tick(db)
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


def _tz():
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _heartbeat_row(db):
    """The single scheduler_heartbeat row (id=1), created on first tick."""
    from app.models import SchedulerHeartbeat

    hb = db.get(SchedulerHeartbeat, 1)
    if hb is None:
        hb = SchedulerHeartbeat(id=1)
        db.add(hb)
    return hb


def _enqueue_briefing(db) -> None:
    from app.jobs import enqueue
    from app.runtime_settings import get_effective

    # A call, not an alarm you have to set. That was the whole idea.
    kind = ("briefing_call" if get_effective(db, "briefing_by_phone")
            and get_effective(db, "outbound_calls_enabled") else "morning_briefing")
    enqueue(db, kind, {}, channel="briefing", actor="scheduler")
    log.info("enqueued %s", kind)


def _briefing_tick(db, now: datetime | None = None) -> None:
    """Enqueue the morning brief when its (effective) time has arrived, catch up a
    missed run, and write the scheduler heartbeat. Runs every worker tick.

    This replaces the old APScheduler cron, which bound the time once at startup —
    so a briefing-time change (runtime overlay, R2) took a redeploy to apply. Now
    the time is read from `get_effective` every tick, so a change takes effect
    within a tick with no restart (health TDD §6, reschedule-on-change).

    Missed-run catch-up (TDD §6): the fire condition is "the scheduled minute has
    *passed* today and we haven't briefed today" — not "the clock equals the
    minute" — so a worker that was down at the exact minute still briefs once when
    it comes back. `last_briefing_date` (owner tz) guards against a double-fire.

    Never raises: a scheduler hiccup must not stop the job queue.
    """
    try:
        from app.runtime_settings import get_effective

        tz = _tz()
        now = (now or datetime.now(tz)).astimezone(tz)
        enabled = bool(get_effective(db, "briefing_enabled"))
        scheduled = now.replace(hour=get_effective(db, "briefing_hour"),
                                minute=get_effective(db, "briefing_minute"),
                                second=0, microsecond=0)

        hb = _heartbeat_row(db)
        if enabled and now >= scheduled and hb.last_briefing_date != now.date():
            # Stamp the guard date BEFORE enqueuing: enqueue() commits, so setting
            # it first means the job and the double-fire guard persist in the SAME
            # transaction. If we set it after, a crash between enqueue's commit and
            # ours would re-fire the brief on restart.
            hb.last_briefing_date = now.date()
            _enqueue_briefing(db)

        hb.beat_at = now
        hb.enabled = enabled
        hb.next_run_at = (scheduled if now < scheduled
                          else scheduled + timedelta(days=1)) if enabled else None
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.error("briefing tick error: %s", e)
        db.rollback()


def _log_briefing_schedule(db) -> None:
    """Preserve the operator-facing log line the runbook points at, computed from
    the effective (overlay-aware) time."""
    from app.runtime_settings import get_effective

    if not get_effective(db, "briefing_enabled"):
        log.info("morning briefing disabled (briefing_enabled=false)")
        return
    log.info("briefing scheduled daily at %02d:%02d %s",
             get_effective(db, "briefing_hour"), get_effective(db, "briefing_minute"),
             settings.calendar_timezone)


def watch(interval: int) -> None:
    log.info("Job worker watching every %ss", interval)
    # Startup heartbeat + missed-run catch-up: if the scheduled minute already
    # passed today and nothing briefed yet, this first tick enqueues it (TDD §6).
    db = SessionLocal()
    try:
        _briefing_tick(db)
        _log_briefing_schedule(db)
    finally:
        db.close()
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
