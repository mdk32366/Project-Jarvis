"""Request log — one coarse row per top-level request (health TDD §9 Phase 2).

Grain: per-REQUEST (one "book me a flight" = one row), vs actions_audit's
per-TOOL. The receipt is written on its OWN short-lived session and committed
immediately, independent of the request's transaction — so a crashed request
still leaves a row (an `in_progress` row that never resolved is itself the
signal). Measured ~4ms/commit on the prod 512MB VM; the voice path orchestrates
in a background task, so this is off the TwiML critical path. A write here must
NEVER break the request — every helper swallows its own errors.

Retention (§11): TIME is the primary policy (90d default — a busy week must not
evict the quiet month you need to investigate); a row-count cap is the safety
valve so a runaway loop can't fill the disk before the time-sweep runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import RequestLog

log = logging.getLogger(__name__)


def start(channel: str, thread_key: str, actor: str, trigger: str) -> int | None:
    """Write the receipt and commit it independently. Returns the row id (to
    `finish` later), or None if the write failed (which must not block the run)."""
    s = SessionLocal()
    try:
        row = RequestLog(channel=channel, thread_key=thread_key, actor=actor,
                         trigger=(trigger or "")[:200], disposition="in_progress")
        s.add(row)
        s.commit()
        return row.id
    except Exception as e:  # noqa: BLE001 — logging must never break a request
        log.warning("request_log start failed: %s", e)
        s.rollback()
        return None
    finally:
        s.close()


def finish(req_id: int | None, disposition: str, duration_ms: int, error: str = "") -> None:
    """Resolve the receipt on a FRESH session — works even if the request's own
    session broke on the error path (that's the whole point of §27)."""
    if req_id is None:
        return
    s = SessionLocal()
    try:
        row = s.get(RequestLog, req_id)
        if row is not None:
            row.disposition = disposition
            row.duration_ms = duration_ms
            row.error_detail = (error or "")[:500]
            s.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("request_log finish failed: %s", e)
        s.rollback()
    finally:
        s.close()


def prune(db: Session) -> int:
    """Time-based sweep (primary) + row-count safety valve. Returns rows deleted."""
    deleted = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.request_log_retention_days)
    deleted += (
        db.query(RequestLog).filter(RequestLog.received_at < cutoff)
        .delete(synchronize_session=False)
    )
    cap = settings.request_log_max_rows
    total = db.query(func.count(RequestLog.id)).scalar() or 0
    if total - deleted > cap:
        # id is strictly monotonic; drop everything older than the newest `cap` rows.
        boundary_id = (
            db.query(RequestLog.id).order_by(RequestLog.id.desc())
            .offset(cap).limit(1).scalar()
        )
        if boundary_id is not None:
            deleted += (
                db.query(RequestLog).filter(RequestLog.id <= boundary_id)
                .delete(synchronize_session=False)
            )
    if deleted:
        db.commit()
        log.info("request_log pruned %d rows", deleted)
    return deleted


def recent_summary(db: Session, limit: int = 200) -> dict:
    """Rollup for self_whoami: dispositions over the recent window + last errors."""
    rows = (
        db.query(RequestLog).order_by(RequestLog.received_at.desc()).limit(limit).all()
    )
    by: dict[str, int] = {}
    for r in rows:
        by[r.disposition] = by.get(r.disposition, 0) + 1
    errors = [
        {"trigger": r.trigger, "error": (r.error_detail or "")[:120],
         "at": r.received_at.isoformat() if r.received_at else None}
        for r in rows if r.disposition == "error"
    ][:5]
    return {"window": len(rows), "by_disposition": by, "recent_errors": errors}
