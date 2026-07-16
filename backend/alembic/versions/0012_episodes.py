"""episodes + episode_quotes — the episodic memory tier (TDD #14)

Revision ID: 0012_episodes
Revises: 0011_google_documents
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_episodes"
down_revision = "0011_google_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("thread_key", sa.String(length=255), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("topics", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("action_items", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("salience", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("embedding", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ref", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_episodes_thread_key"), "episodes", ["thread_key"])
    op.create_index(op.f("ix_episodes_occurred_on"), "episodes", ["occurred_on"])
    op.create_index(op.f("ix_episodes_source_ref"), "episodes", ["source_ref"])

    op.create_table(
        "episode_quotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="key_fact"),
        sa.Column("turn_ref", sa.String(length=64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_episode_quotes_episode_id"), "episode_quotes", ["episode_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_episode_quotes_episode_id"), table_name="episode_quotes")
    op.drop_table("episode_quotes")
    op.drop_index(op.f("ix_episodes_source_ref"), table_name="episodes")
    op.drop_index(op.f("ix_episodes_occurred_on"), table_name="episodes")
    op.drop_index(op.f("ix_episodes_thread_key"), table_name="episodes")
    op.drop_table("episodes")
