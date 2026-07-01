"""Phase 1 schema: memories.embedding, jobs table, pgvector store.

Written defensively (IF [NOT] EXISTS + to_regclass guards) so it is safe whether
run against a fresh database or the already-deployed Phase 0 schema. The app also
calls Base.metadata.create_all() and vectorstore.ensure_ready() at boot; this
migration guarantees the one thing create_all cannot do: ALTER the existing
`memories` table to add the embedding column.

Revision ID: 0001_phase1
Revises:
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Dev/test (SQLite) uses create_all(); nothing to do here.
        return

    # 1) Add embedding column to the pre-existing memories table (if present).
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.memories') IS NOT NULL THEN
            ALTER TABLE memories ADD COLUMN IF NOT EXISTS embedding TEXT DEFAULT '';
          END IF;
        END $$;
        """
    )

    # 2) Durable job queue.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id SERIAL PRIMARY KEY,
            kind VARCHAR(64) NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status VARCHAR(16) NOT NULL DEFAULT 'queued',
            result TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            channel VARCHAR(32) NOT NULL DEFAULT '',
            thread_key VARCHAR(255) NOT NULL DEFAULT '',
            actor VARCHAR(255) NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_kind ON jobs (kind);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);")

    # 3) pgvector store (also ensured at app boot; done here so it's ready first).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.memories') IS NOT NULL THEN
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
                embedding vector(1024)
            );
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS memory_embeddings;")
    op.execute("DROP TABLE IF EXISTS jobs;")
    op.execute("ALTER TABLE IF EXISTS memories DROP COLUMN IF EXISTS embedding;")
