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
from app.jobs import process_available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def run_once() -> int:
    db = SessionLocal()
    try:
        return process_available(db)
    finally:
        db.close()


def watch(interval: int) -> None:
    log.info("Job worker watching every %ss", interval)
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
