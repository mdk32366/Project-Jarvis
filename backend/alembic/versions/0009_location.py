"""location_pings

Revision ID: 0009_location
Revises: 0008_watches
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_location"
down_revision = "0008_watches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_pings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="phone"),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("location_pings")
