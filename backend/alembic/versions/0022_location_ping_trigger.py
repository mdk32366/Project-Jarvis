"""location_pings.trigger

How a fix arrived: "pull" (answered a request) or "manual" (the owner pressed the
shortcut). See TDD §5.2/§6.6 — descriptive only, nullable, and read by NO health
check. Attribution lives on `location_requests`; a client-supplied field must
never be load-bearing for health when the client is the thing whose reliability is
in question.

Separate from 0021 because 0021 is already deployed. Nullable with no backfill:
pings recorded before this column existed have no honest value to claim.

Revision ID: 0022_location_ping_trigger
Revises: 0021_location_request
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_location_ping_trigger"
down_revision = "0021_location_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("location_pings", sa.Column("trigger", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("location_pings", "trigger")
