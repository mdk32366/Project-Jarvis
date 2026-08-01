"""planning_session, planning_note

TDD #2 (docs/TDD-planning-sessions.md §6.3). The draft names this
`0023_planning_sessions`; that slot went to 0023_relay_accepted, and 0024-0026
went to projects, the capability rollup, and the github write log. Confirmed
against live head (`alembic heads` -> 0026_github_write_log) on 2026-08-01
rather than trusted from the draft — the fourth stale number in this series.

Carried forward: project inception still says `0026` in its draft and is now
stale too. It rebases off live head at its own build time.

Order matters: planning_note carries an FK to planning_session, and
planning_session carries FKs to project (0024) and project_document (0024),
both of which exist by this point in the chain.

Revision ID: 0027_planning_sessions
Revises: 0026_github_write_log
Create Date: 2026-08-01
"""

from alembic import op

from app.models import PlanningNote, PlanningSession

revision = "0027_planning_sessions"
down_revision = "0026_github_write_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    PlanningSession.__table__.create(bind=op.get_bind(), checkfirst=True)
    PlanningNote.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    PlanningNote.__table__.drop(bind=op.get_bind(), checkfirst=True)
    PlanningSession.__table__.drop(bind=op.get_bind(), checkfirst=True)
