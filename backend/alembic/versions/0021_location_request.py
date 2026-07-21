"""location_requests table + location_pings.request_id

The location schedule inversion (docs/TDD-location-pull-inversion.md §5.3): the
server now ASKS for a fix and the phone answers, so the ask needs a record.

`location_pings` is created by 0009_location — a real migration, not one of the
create_all-only bootstrap tables — so a plain add_column is safe on both a fresh
and an existing database (same reasoning as 0016's ideas.promoted_url).

No backfill. Historical pings keep a null `request_id` honestly: they were
unsolicited, and pretending otherwise would invent correlation that never existed.

Revision ID: 0021_location_request
Revises: 0020_request_log
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

from app.models import LocationRequest

revision = "0021_location_request"
down_revision = "0020_request_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    LocationRequest.__table__.create(bind=op.get_bind(), checkfirst=True)
    op.add_column("location_pings", sa.Column("request_id", sa.Integer(), nullable=True))
    op.create_index("ix_location_pings_request_id", "location_pings", ["request_id"])
    # SQLite cannot add a foreign key via ALTER (it has no such statement); prod is
    # Postgres, where the constraint is worth having. Dev/test SQLite databases are
    # built by create_all, which declares the FK inline, so nothing is lost there.
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_location_pings_request_id", "location_pings", "location_requests",
            ["request_id"], ["id"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_location_pings_request_id", "location_pings", type_="foreignkey")
    op.drop_index("ix_location_pings_request_id", table_name="location_pings")
    op.drop_column("location_pings", "request_id")
    LocationRequest.__table__.drop(bind=op.get_bind(), checkfirst=True)
