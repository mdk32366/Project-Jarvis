"""ideas.promoted_url

Records the GitHub repo an idea was promoted into (create_project_from_idea).
Non-empty means "already a project" so a second promotion is refused.

`ideas` is created by 0004_agents_expansion (which runs before this), so — like
0006's tasks.google_id — a plain add_column is safe on both a fresh and an
existing database; it is NOT one of the create_all-only bootstrap tables.

Revision ID: 0016_idea_promoted
Revises: 0015_pending_batch
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_idea_promoted"
down_revision = "0015_pending_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ideas",
        sa.Column("promoted_url", sa.String(length=300), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("ideas", "promoted_url")
