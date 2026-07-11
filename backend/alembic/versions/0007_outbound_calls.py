"""outbound_calls

Revision ID: 0007_outbound
Revises: 0006_task_google
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_outbound"
down_revision = "0006_task_google"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="callback"),
        sa.Column("opening", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("call_sid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_outbound_calls_status"), "outbound_calls", ["status"])
    op.create_index(op.f("ix_outbound_calls_call_sid"), "outbound_calls", ["call_sid"])


def downgrade() -> None:
    op.drop_index(op.f("ix_outbound_calls_call_sid"), table_name="outbound_calls")
    op.drop_index(op.f("ix_outbound_calls_status"), table_name="outbound_calls")
    op.drop_table("outbound_calls")
