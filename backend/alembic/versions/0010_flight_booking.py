"""flight_booking

Revision ID: 0010_flight_booking
Revises: 0009_location
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_flight_booking"
down_revision = "0009_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flight_offers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_key", sa.String(length=255), nullable=False),
        sa.Column("offer_id", sa.String(length=128), nullable=False),
        sa.Column("total_amount", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("total_currency", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("carrier", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("route", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("depart_at", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_flight_offers_thread_key"), "flight_offers", ["thread_key"])
    op.create_index(op.f("ix_flight_offers_offer_id"), "flight_offers", ["offer_id"], unique=True)

    # pending_confirmations predates the migration chain (created historically
    # via Base.metadata.create_all(), same as every table 0001 through 0003
    # implicitly assume) — no earlier migration creates it, so on a from-scratch
    # SQLite run (dev/test uses create_all, never a real ALTER chain) this table
    # simply does not exist yet at this point in alembic's own history. Guard to
    # Postgres, matching 0001's pattern: prod already has the table from
    # create_all-at-boot; SQLite dev/test gets it (with these columns already
    # present) from create_all reading the current models, so there is nothing
    # for alembic to do there either way.
    # Guard on table existence too, not just dialect: pending_confirmations is a
    # create_all-only bootstrap table, so on a from-scratch Postgres it does not
    # exist yet here (0013_baseline creates it afterward, already carrying these
    # columns). Existence guard + IF NOT EXISTS keeps `upgrade head` from
    # aborting on a fresh/restored database.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regclass('public.pending_confirmations') IS NOT NULL THEN
                ALTER TABLE pending_confirmations ADD COLUMN IF NOT EXISTS code_deadline TIMESTAMPTZ;
                ALTER TABLE pending_confirmations ADD COLUMN IF NOT EXISTS code_attempts INTEGER NOT NULL DEFAULT 0;
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE IF EXISTS pending_confirmations DROP COLUMN IF EXISTS code_attempts;")
        op.execute("ALTER TABLE IF EXISTS pending_confirmations DROP COLUMN IF EXISTS code_deadline;")
    op.drop_index(op.f("ix_flight_offers_offer_id"), table_name="flight_offers")
    op.drop_index(op.f("ix_flight_offers_thread_key"), table_name="flight_offers")
    op.drop_table("flight_offers")
