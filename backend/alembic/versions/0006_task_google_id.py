"""tasks.google_id

Revision ID: 0006_task_google
Revises: 0005_contacts
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_task_google"
down_revision = "0005_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("google_id", sa.String(length=128), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("tasks", "google_id")
