"""capability, capability_member, evaluator_heartbeat

Capability rollup (docs/TDD-capability-status.md §5) — the v1 seed ratified in
BUILD-ORDERS-2026-07-31-capability-seed-ratification.md.

The TDD series reserved 0025 for planning sessions; that build has not landed and
numbers in the series are indicative, not reserved (the 0024 lesson). Planning
sessions and github writes shift to 0026 / 0027.

All three tables are NEW, so `Model.__table__.create(checkfirst=True)` is safe on
both a fresh and an existing database. No column is altered on a pre-existing
table, so the 0024 rename hazard does not apply here.

NO seed data is written by this migration. `seed_health_topology` reconciles the
capability rows from code on every startup, exactly as it does for components —
putting the seed in a migration would freeze it at write time and reintroduce the
stale-reference-data bug the reconciling seed exists to prevent.

Revision ID: 0025_capability_rollup
Revises: 0024_projects
Create Date: 2026-07-31
"""

from alembic import op

from app.models import Capability, CapabilityMember, EvaluatorHeartbeat

revision = "0025_capability_rollup"
down_revision = "0024_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Capability.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityMember.__table__.create(bind=op.get_bind(), checkfirst=True)
    EvaluatorHeartbeat.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    EvaluatorHeartbeat.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityMember.__table__.drop(bind=op.get_bind(), checkfirst=True)
    Capability.__table__.drop(bind=op.get_bind(), checkfirst=True)
