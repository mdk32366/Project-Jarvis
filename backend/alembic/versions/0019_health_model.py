"""health relational model: component, remediation, health_result (TDD §4, R4).

Idempotent, dialect-agnostic `__table__.create(checkfirst=True)` per the
established convention — a no-op where tables exist, and it materializes them on a
fresh Postgres (the CI `migrations` job). Seeding is done in code at startup
(`app.health.seed_health_topology`), not in the migration.

Revision ID: 0019_health_model
Revises: 0018_scheduler_heartbeat
Create Date: 2026-07-19
"""

from alembic import op

from app.models import Component, HealthResult, Remediation

revision = "0019_health_model"
down_revision = "0018_scheduler_heartbeat"
branch_labels = None
depends_on = None

_TABLES = [Component.__table__, Remediation.__table__, HealthResult.__table__]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind=bind, checkfirst=True)
