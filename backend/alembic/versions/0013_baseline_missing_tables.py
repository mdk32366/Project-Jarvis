"""baseline: create tables that historically came only from create_all

Nine tables — users, conversations, messages, persona_profile, preferences,
memories, contacts_whitelist, actions_audit, pending_confirmations — were never
created by any migration; they existed only because the api process runs
Base.metadata.create_all() at boot. That left `alembic upgrade head` unable to
bootstrap a fresh or restored Postgres on its own, and Fly's release_command
(`alembic upgrade head`) runs BEFORE any app process boots — so a rebuild or a
second environment would abort the deploy on the first ALTER against a missing
table (audit H2 / M1). The concrete failure was 0010 adding columns to
pending_confirmations, which no migration creates.

This migration closes the gap by creating any missing model table, idempotently.
It reuses the SQLAlchemy models as the single source of truth (no hand-copied
DDL to drift), and `checkfirst=True` means it creates ONLY tables that are
absent — the existing production schema is left completely untouched.

Ordering note: 0010's add_column on pending_confirmations is now existence-
guarded, so on a fresh DB it no-ops and this migration creates that table
complete with those columns. (`tasks` is NOT in this set — 0004 creates it and
0006 alters it, a chain that already bootstraps cleanly.)

Revision ID: 0013_baseline
Revises: 0012_episodes
"""

from alembic import op

from app.database import Base
from app import models  # noqa: F401  — registers every table on Base.metadata

revision = "0013_baseline"
down_revision = "0012_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # checkfirst=True: create only missing tables. On an existing database every
    # table already exists, so this is a no-op; on a fresh/restored one it
    # materializes the create_all-only tables that no migration otherwise makes.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Non-destructive baseline — it only ever fills in absent tables, so there is
    # nothing safe or meaningful to drop on downgrade.
    pass
