"""request_log table — one coarse row per top-level request (TDD §9 Phase 2).

Idempotent, dialect-agnostic `__table__.create(checkfirst=True)` per convention.

Revision ID: 0020_request_log
Revises: 0019_health_model
Create Date: 2026-07-19
"""

from alembic import op

from app.models import RequestLog

revision = "0020_request_log"
down_revision = "0019_health_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    RequestLog.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    RequestLog.__table__.drop(bind=op.get_bind(), checkfirst=True)
