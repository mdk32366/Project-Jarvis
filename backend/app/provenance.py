"""Build + deploy provenance — "what am I running?" (health TDD §9 Phase 1).

Commit and build time are BAKED at build time (Dockerfile ARG → APP_COMMIT /
APP_BUILD_TIME), never a live `git` shell-out — the container has no git. Fly
injects the deploy metadata (app / region / machine / version) at runtime.
`in_service_days` anchors on the first user row (seeded on the first deploy) — a
stable "since inception" number that survives redeploys, unlike a per-build clock.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def provenance(db: Session) -> dict:
    commit = os.environ.get("APP_COMMIT", "dev")
    first_user = db.query(User).order_by(User.created_at.asc()).first()
    in_service_days = None
    if first_user and first_user.created_at:
        anchor = first_user.created_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        in_service_days = (_now() - anchor).days
    return {
        "commit": commit[:12] if commit not in ("", "dev") else commit,
        "build_time": os.environ.get("APP_BUILD_TIME", "unknown"),
        "app": os.environ.get("FLY_APP_NAME", "dev"),
        "region": os.environ.get("FLY_REGION", "local"),
        "machine": os.environ.get("FLY_MACHINE_ID", ""),
        "version": os.environ.get("FLY_MACHINE_VERSION", ""),
        "image": os.environ.get("FLY_IMAGE_REF", ""),
        "in_service_days": in_service_days,
    }
