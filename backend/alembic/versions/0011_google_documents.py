"""google_documents

Revision ID: 0011_google_documents
Revises: 0010_flight_booking
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_google_documents"
down_revision = "0010_flight_booking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="doc"),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("thread_key", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_google_documents_doc_id"), "google_documents", ["doc_id"], unique=True)
    op.create_index(op.f("ix_google_documents_thread_key"), "google_documents", ["thread_key"])


def downgrade() -> None:
    op.drop_index(op.f("ix_google_documents_thread_key"), table_name="google_documents")
    op.drop_index(op.f("ix_google_documents_doc_id"), table_name="google_documents")
    op.drop_table("google_documents")
