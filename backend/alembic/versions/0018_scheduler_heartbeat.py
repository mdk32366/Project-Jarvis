"""scheduler_heartbeat table (health TDD §5.2 / §6, roadmap R3).

Backs the briefing scheduler's proof-of-life + missed-run catch-up. Idempotent,
dialect-agnostic `__table__.create(checkfirst=True)` — a no-op where it exists,
and it materializes the table on a fresh Postgres (the CI `migrations` job).

Revision ID: 0018_scheduler_heartbeat
Revises: 0017_runtime_settings
Create Date: 2026-07-19
"""

from alembic import op

from app.models import SchedulerHeartbeat

revision = "0018_scheduler_heartbeat"
down_revision = "0017_runtime_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    SchedulerHeartbeat.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SchedulerHeartbeat.__table__.drop(bind=op.get_bind(), checkfirst=True)
