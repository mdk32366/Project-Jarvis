"""voice_turns.notify_email

When a caller is handed off (held past the budget, or asked to be called/emailed)
while a turn is still running, run_turn emails the finished answer on completion.
That needs a durable per-turn flag — not in-process state, which the voice_turns
table exists precisely to avoid (a Fly restart mid-call would lose it).

`voice_turns` is created by 0003 (a plain migration table, not one of the
create_all-only bootstrap tables handled by 0013_baseline), so a portable
add_column is safe and runs on both SQLite and Postgres.

Revision ID: 0014_voice_notify
Revises: 0013_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_voice_notify"
down_revision = "0013_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_turns",
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("voice_turns", "notify_email")
