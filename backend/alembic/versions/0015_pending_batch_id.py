"""pending_confirmations.batch_id

Groups the gated actions created in one request so a compound "do this, that, and
the other" can be read back together and confirmed with a single reply
(TDD-multi-action-buffering). NULL for a standalone action.

`pending_confirmations` is a create_all-only bootstrap table (created by
0013_baseline, not an early migration), so guard the add_column on table
existence + IF NOT EXISTS on Postgres, exactly like 0010's columns on the same
table. SQLite dev/test builds the column from the model via create_all.

Revision ID: 0015_pending_batch
Revises: 0014_voice_notify
"""

from alembic import op

revision = "0015_pending_batch"
down_revision = "0014_voice_notify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite dev/test uses create_all
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regclass('public.pending_confirmations') IS NOT NULL THEN
            ALTER TABLE pending_confirmations ADD COLUMN IF NOT EXISTS batch_id VARCHAR(36);
            CREATE INDEX IF NOT EXISTS ix_pending_confirmations_batch_id
              ON pending_confirmations (batch_id);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_pending_confirmations_batch_id;")
    op.execute("ALTER TABLE IF EXISTS pending_confirmations DROP COLUMN IF EXISTS batch_id;")
