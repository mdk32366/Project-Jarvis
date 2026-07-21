"""location_requests.dispatch_ok -> relay_accepted, dispatch_error -> relay_error

TDD-location-pull-inversion §12. The old names claimed a leg the column could not
see: `dispatch_ok` recorded only that the AutoRemote relay returned HTTP 200, and
the relay answers 200 to everything — reporting the real outcome in the body. It
answered `NotRegistered` on every send from the PR #36 deploy until 2026-07-21
while `dispatch_ok` read True throughout.

Renamed rather than dropped: the existing rows are real history of what the relay
was asked, and the values stay meaningful under the honest name.

WHY THE RENAME IS CONDITIONAL — a hazard of this repo's migration convention,
worth understanding before writing the next rename:

    `0021` creates this table with `LocationRequest.__table__.create()`. That
    reflects the model as it is TODAY, not as it was at revision 0021. The moment
    the model renamed these columns, 0021 began creating fresh databases with
    `relay_accepted`/`relay_error` already in place — so on an empty database this
    migration has nothing to rename, while on the existing production database it
    has everything to rename. Both are correct states; the migration has to cope
    with either.

    Unconditional `alter_column` failed the CI migration gate on fresh Postgres
    with `column "dispatch_ok" does not exist`. That gate exists precisely because
    the pytest suite builds its schema with `create_all` and never runs this
    chain, so bootstrap bugs are otherwise invisible.

    Any `__table__.create()`-based table has this property. Guard renames on the
    old column actually being there.

Revision ID: 0023_relay_accepted
Revises: 0022_location_ping_trigger
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_relay_accepted"
down_revision = "0022_location_ping_trigger"
branch_labels = None
depends_on = None

_TABLE = "location_requests"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _rename(old: str, new: str) -> None:
    cols = _columns()
    if old in cols and new not in cols:
        op.alter_column(_TABLE, old, new_column_name=new)


def upgrade() -> None:
    _rename("dispatch_ok", "relay_accepted")
    _rename("dispatch_error", "relay_error")


def downgrade() -> None:
    _rename("relay_accepted", "dispatch_ok")
    _rename("relay_error", "dispatch_error")
