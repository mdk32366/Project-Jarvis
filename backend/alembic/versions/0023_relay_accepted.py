"""location_requests.dispatch_ok -> relay_accepted, dispatch_error -> relay_error

TDD-location-pull-inversion §12. The old names claimed a leg the column could not
see: `dispatch_ok` recorded only that the AutoRemote relay returned HTTP 200, and
the relay answers 200 to everything — reporting the real outcome in the body. It
answered `NotRegistered` on every send from the PR #36 deploy until 2026-07-21
while `dispatch_ok` read True throughout.

Renamed rather than dropped: the existing rows are real history of what the relay
was asked, and the values remain meaningful under the honest name. `alter_column`
with `new_column_name` is supported on both Postgres and modern SQLite (3.25+,
which is every SQLite this project runs on), so no table rebuild is needed.

Revision ID: 0023_relay_accepted
Revises: 0022_location_ping_trigger
Create Date: 2026-07-21
"""

from alembic import op

revision = "0023_relay_accepted"
down_revision = "0022_location_ping_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("location_requests", "dispatch_ok", new_column_name="relay_accepted")
    op.alter_column("location_requests", "dispatch_error", new_column_name="relay_error")


def downgrade() -> None:
    op.alter_column("location_requests", "relay_accepted", new_column_name="dispatch_ok")
    op.alter_column("location_requests", "relay_error", new_column_name="dispatch_error")
