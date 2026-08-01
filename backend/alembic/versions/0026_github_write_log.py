"""github_write_log

TDD #3 (docs/TDD-repo-scaffolding.md §5, as reconciled in §11.5). The TDD names
this `0024_github_writes`; that slot went to 0024_projects and 0025 to the
capability rollup, so it lands at **0026**. Confirmed against live head
(`alembic heads` → 0025_capability_rollup) on 2026-08-01 rather than trusted
from the draft — the third time in this series that a written number was stale.

Carried forward for whoever writes the next one: TDD #2 (planning sessions) and
project inception BOTH still say "0026" in their drafts. Both are now stale and
must rebase off live head at their own build time. Numbers in the series are
indicative, never reserved.

The table lands now, ahead of the writers that fill it (steps 3/5), so those
steps have somewhere to write and the health check's substrate exists before
anything depends on it.

Revision ID: 0026_github_write_log
Revises: 0025_capability_rollup
Create Date: 2026-08-01
"""

from alembic import op

from app.models import GithubWriteLog

revision = "0026_github_write_log"
down_revision = "0025_capability_rollup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `Model.__table__.create(checkfirst=True)` is the convention here: idempotent
    # and dialect-agnostic, so this is safe on a fresh Postgres (the CI gate) and
    # on a database where the table somehow already exists.
    GithubWriteLog.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    GithubWriteLog.__table__.drop(bind=op.get_bind(), checkfirst=True)
