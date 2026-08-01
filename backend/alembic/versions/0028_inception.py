"""inception: milestone dates + replan / baseline_reset / plan_risk / plan_assumption

TDD project-inception §5. The reconstructed TDD says `0026`; that was consumed
by 0026_github_write_log, and 0027 by planning sessions. Confirmed against live
head (`alembic heads` -> 0027_planning_sessions) on 2026-08-01 — the FIFTH stale
number in this series, and the reason the rule is "confirm, never trust".

All additive. `Milestone` had no date columns, so there is nothing to reconcile —
this is a plain add_column on a table created by 0024_projects (a real migration,
not one of the create_all-only bootstrap tables), so it is safe on both a fresh
and an existing database.

SCHEMA ONLY. Dates do not MOVE until step 3; this makes the columns exist and
nothing else.

Revision ID: 0028_inception
Revises: 0027_planning_sessions
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

from app.models import BaselineReset, PlanAssumption, PlanRisk, Replan

revision = "0028_inception"
down_revision = "0027_planning_sessions"
branch_labels = None
depends_on = None

# `current_date` collides with the SQL CURRENT_DATE keyword. SQLAlchemy quotes
# reserved identifiers, so this is expected to be fine — and the CI fresh-Postgres
# migration gate is what proves it rather than an assumption in a comment.
_MILESTONE_COLS = (
    ("baseline_date", sa.Date()),
    ("current_date", sa.Date()),
    ("date_status", sa.String(length=16)),
)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for name, type_ in _MILESTONE_COLS:
        if not _has_column("milestone", name):
            kw = {"server_default": "none"} if name == "date_status" else {}
            op.add_column("milestone", sa.Column(name, type_, nullable=True, **kw))

    # Order matters: replan and plan_risk carry FKs to milestone; baseline_reset
    # and plan_assumption to project. Both parents predate this migration.
    Replan.__table__.create(bind=op.get_bind(), checkfirst=True)
    BaselineReset.__table__.create(bind=op.get_bind(), checkfirst=True)
    PlanRisk.__table__.create(bind=op.get_bind(), checkfirst=True)
    PlanAssumption.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    PlanAssumption.__table__.drop(bind=op.get_bind(), checkfirst=True)
    PlanRisk.__table__.drop(bind=op.get_bind(), checkfirst=True)
    BaselineReset.__table__.drop(bind=op.get_bind(), checkfirst=True)
    Replan.__table__.drop(bind=op.get_bind(), checkfirst=True)

    for name, _ in _MILESTONE_COLS:
        if _has_column("milestone", name):
            op.drop_column("milestone", name)
