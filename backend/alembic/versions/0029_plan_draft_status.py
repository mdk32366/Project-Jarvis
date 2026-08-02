"""plan_risk/plan_assumption.plan_status + plan_assumption.surfaced_at

Inception step 6 (the §11 atomicity resolution) and step 5 (surface-once).

WHY A ROW-LEVEL MARKER AND NOT A SESSION FLAG. The order left the choice open —
"row-level `plan_status` vs. session flag, pick the lighter". A session flag
CANNOT WORK here, and that is decisive rather than a matter of taste: the rows
emit seeds (`plan_risk`, `plan_assumption`) carry `project_id`, not
`session_id`. There is no link from a seeded row back to the session that
seeded it, so a flag on `planning_session` could never identify WHICH rows were
drafts. Making it work would mean adding a session FK to every seeded table —
strictly more schema than the two columns below.

`surfaced_at` is stamped rather than inferred so "I already told you about this"
survives a restart; an assumption whose broken-ness is re-announced every
morning is one the owner learns to skip past.

Both default to the safe value (`live` / NULL), so every existing row and every
non-emit creation path is unaffected.

Revision ID: 0029_plan_draft_status
Revises: 0028_inception
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_plan_draft_status"
down_revision = "0028_inception"
branch_labels = None
depends_on = None

_ADDS = (
    ("plan_risk", "plan_status", sa.String(length=8), "live"),
    ("plan_assumption", "plan_status", sa.String(length=8), "live"),
)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, column, type_, default in _ADDS:
        if not _has_column(table, column):
            op.add_column(table, sa.Column(column, type_, nullable=True,
                                           server_default=default))
    if not _has_column("plan_assumption", "surfaced_at"):
        op.add_column("plan_assumption",
                      sa.Column("surfaced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column("plan_assumption", "surfaced_at"):
        op.drop_column("plan_assumption", "surfaced_at")
    for table, column, _, _ in _ADDS:
        if _has_column(table, column):
            op.drop_column(table, column)
