"""tasks, ideas, trips

Revision ID: 0004_expansion
Revises: 0003_voice_turns
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_expansion"
down_revision = "0003_voice_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)

    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("committed_sha", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("commit_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("carrier", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("confirmation", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("origin", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("destination", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("depart_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flight_no", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("seat", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trips_confirmation"), "trips", ["confirmation"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trips_confirmation"), table_name="trips")
    op.drop_table("trips")
    op.drop_table("ideas")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_table("tasks")
