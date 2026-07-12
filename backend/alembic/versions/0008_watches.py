"""watches

Revision ID: 0008_watches
Revises: 0007_outbound
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_watches"
down_revision = "0007_outbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("tool_args", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("opening", sa.Text(), nullable=False),
        sa.Column("every_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("recurring", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watches_status"), "watches", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_watches_status"), table_name="watches")
    op.drop_table("watches")
