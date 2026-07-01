"""Phase 1: agent_configs table (data-driven specialist roster).

New table only, so create_all() also handles it at boot; this migration keeps the
prod schema explicit. Defensive/idempotent.

Revision ID: 0002_agents
Revises: 0001_phase1
Create Date: 2026-07-01
"""

from alembic import op

revision = "0002_agents"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_configs (
            id SERIAL PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            tools TEXT NOT NULL DEFAULT '[]',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_configs_name ON agent_configs (name);")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS agent_configs;")
