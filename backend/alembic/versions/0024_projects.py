"""project, milestone, project_document + ideas.status

TDD #1 (docs/TDD-project-tracking.md §5.5). The TDD names this 0022_projects.
That slot went to 0022_location_ping_trigger the same morning, and 0023 then went
to 0023_relay_accepted while this branch sat parked — so the series lands two
later than written: **projects 0024, planning sessions 0025, github writes 0026**.
Numbers in the TDD series are indicative, not reserved; check the actual head
before writing the next one.

`ideas` is created by 0004_agents_expansion, a real migration and NOT one of the
create_all-only bootstrap tables, so a plain add_column is safe on both a fresh
and an existing database (the 0016 reasoning).

`ideas.status` is add-if-absent per TDD §5.4: the table dates to 0004 and was
flagged as possibly drifted. Verified against the live model on 2026-07-21 — the
column does NOT exist — but the guard is kept so this migration is safe to run
against a database where it somehow does.

No backfill. Existing arcs get entered by hand or not at all.

Revision ID: 0024_projects
Revises: 0023_relay_accepted
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

from app.models import Milestone, Project, ProjectDocument

revision = "0024_projects"
down_revision = "0023_relay_accepted"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Order matters: milestone/project_document carry FKs to project.
    Project.__table__.create(bind=op.get_bind(), checkfirst=True)
    Milestone.__table__.create(bind=op.get_bind(), checkfirst=True)
    ProjectDocument.__table__.create(bind=op.get_bind(), checkfirst=True)

    if not _has_column("ideas", "status"):
        op.add_column(
            "ideas",
            sa.Column("status", sa.String(length=16), nullable=False, server_default="idea"),
        )


def downgrade() -> None:
    if _has_column("ideas", "status"):
        op.drop_column("ideas", "status")
    ProjectDocument.__table__.drop(bind=op.get_bind(), checkfirst=True)
    Milestone.__table__.drop(bind=op.get_bind(), checkfirst=True)
    Project.__table__.drop(bind=op.get_bind(), checkfirst=True)
