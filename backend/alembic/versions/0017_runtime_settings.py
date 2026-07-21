"""runtime_settings overlay table (health TDD §7, roadmap R2).

Creates the `runtime_settings` table that backs the behavioral-settings overlay
(`app.runtime_settings.get_effective`). Uses the model's own table definition
with `checkfirst=True`, so it is idempotent and dialect-agnostic: a no-op where
the table already exists, and it materializes it on a fresh Postgres (the CI
`migrations` job, which runs the chain against an empty DB with no create_all).

Revision ID: 0017_runtime_settings
Revises: 0016_idea_promoted
Create Date: 2026-07-19
"""

from alembic import op

from app.models import RuntimeSetting

revision = "0017_runtime_settings"
down_revision = "0016_idea_promoted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    RuntimeSetting.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    RuntimeSetting.__table__.drop(bind=op.get_bind(), checkfirst=True)
