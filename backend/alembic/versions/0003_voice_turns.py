"""add voice_turns

Backing store for the voice channel's async turn results.

The orchestrator can exceed Twilio's ~15s webhook timeout, so /api/voice/gather
returns TwiML immediately and orchestrates in a BackgroundTask; /api/voice/poll
collects the result from this table.

A DB table rather than in-process state: today min_machines_running=1 so
consecutive webhooks hit the same `api` machine, but that is a config value that
will change for unrelated reasons, and a Fly restart mid-call has the same
effect. In-memory state fails intermittently and presents as "voice randomly
hangs up."

Revision ID: 0003_voice_turns
Revises: 0002_agents
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_voice_turns"
down_revision = "0002_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("call_sid", sa.String(length=64), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False, server_default="0"),
        # pending | done | error
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("user_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("reply", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_sid", "turn", name="uq_voice_turn"),
    )
    op.create_index(op.f("ix_voice_turns_call_sid"), "voice_turns", ["call_sid"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_voice_turns_call_sid"), table_name="voice_turns")
    op.drop_table("voice_turns")
