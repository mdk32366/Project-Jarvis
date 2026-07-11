"""contacts

Revision ID: 0005_contacts
Revises: 0004_expansion
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_contacts"
down_revision = "0004_expansion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contacts_name"), "contacts", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contacts_name"), table_name="contacts")
    op.drop_table("contacts")
