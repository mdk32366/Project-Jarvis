"""Relational health model — inventory, remediation join, tool->component map,
reconciliation (health TDD §4, roadmap R4). Pure data + seeding; no checks yet.
"""

from datetime import datetime, timezone

from app.health import (
    component_for_tool, get_runbook, registry_discrepancies, seed_health_topology,
)
from app.models import Component, HealthResult, Remediation


# ── §4.1 inventory seeded from the topology (TDD test #28) ───────────────────

def test_seed_inventory_covers_the_topology(db):
    seed_health_topology(db)
    names = {c.name for c in db.query(Component).all()}
    # all 9 agents
    for a in ("researcher", "finance", "archivist", "infra", "secretary",
              "travel", "navigator", "netstatus", "scheduling"):
        assert a in names
    # external APIs + the trunk subsystems + both halves of the location split
    for x in ("tavily", "duffel", "google_oauth", "google_calendar_svcacct", "twilio",
              "anthropic_api", "postgres", "worker_scheduler",
              "location_pull_scheduler", "location_responsiveness"):
        assert x in names


def test_trunk_is_multi_blast_radius(db):
    seed_health_topology(db)
    for t in ("anthropic_api", "postgres", "worker_scheduler", "email_ingest"):
        assert db.get(Component, t).blast_radius == "multi"
    assert db.get(Component, "researcher").blast_radius == "single"


def test_heartbeat_check_config_carries_the_threshold(db):
    """The staleness threshold is seeded as check config (per the owner's call),
    not hardcoded in the check."""
    import json
    seed_health_topology(db)
    cfg = json.loads(db.get(Component, "worker_scheduler").check_config)
    assert cfg["stale_seconds"] == 300


# ── reconciliation, not append (the seed_agents lesson) ──────────────────────

def test_seed_reconciles_stale_rows(db):
    seed_health_topology(db)
    # Simulate a stale row: wrong kind/description from an older seed.
    row = db.get(Component, "duffel")
    row.kind = "WRONG"
    row.description = "stale"
    db.commit()
    seed_health_topology(db)                       # second pass must FIX it, not skip
    row = db.get(Component, "duffel")
    assert row.kind == "external_api"
    assert row.description != "stale"


def test_seed_is_idempotent(db):
    seed_health_topology(db)
    n1 = db.query(Component).count()
    r1 = db.query(Remediation).count()
    seed_health_topology(db)
    assert db.query(Component).count() == n1        # no duplicates
    assert db.query(Remediation).count() == r1


def test_registry_discrepancies_reports_drift(db):
    seed_health_topology(db)
    # A clean seed matches the code roster exactly.
    disc = registry_discrepancies(db)
    assert disc["agents_in_code_not_seeded"] == []
    assert disc["agents_seeded_not_in_code"] == []
    # Drop an agent component -> it surfaces as a discrepancy, not a silent gap.
    db.delete(db.get(Component, "scheduling"))
    db.commit()
    assert "scheduling" in registry_discrepancies(db)["agents_in_code_not_seeded"]


# ── remediation join (TDD #29, #31) ──────────────────────────────────────────

def test_fault_joins_to_runbook(db):
    seed_health_topology(db)
    rem = get_runbook(db, "worker_scheduler", "heartbeat_stale")
    assert rem is not None
    assert "fly apps restart" in rem.runbook
    assert rem.severity == "critical"


def test_missing_runbook_degrades_gracefully(db):
    seed_health_topology(db)
    # A fault with no seeded row returns None (caller shows a generic message),
    # never a crash.
    assert get_runbook(db, "duffel", "no_such_fault") is None


def test_runbook_is_runtime_editable(db):
    """Edit a row -> the new text surfaces, no redeploy (TDD #30)."""
    seed_health_topology(db)
    rem = get_runbook(db, "duffel", "401")
    rem.runbook = "EDITED: rotate the key"
    db.commit()
    assert get_runbook(db, "duffel", "401").runbook == "EDITED: rotate the key"


# ── tool -> component map (TDD #36) ──────────────────────────────────────────

def test_tool_component_map_resolves(db):
    assert component_for_tool("calendar_lookup") == "google_calendar_svcacct"
    assert component_for_tool("web_search") == "tavily"
    assert component_for_tool("search_flights") == "duffel"
    # agent-prefixed audit rows resolve to the same component
    assert component_for_tool("researcher:web_search") == "tavily"
    assert component_for_tool("scheduling:calendar_lookup") == "google_calendar_svcacct"
    # an unmapped tool is None, not a crash
    assert component_for_tool("get_current_datetime") is None


# ── health_result is transient (TDD #32) ─────────────────────────────────────

def test_health_result_is_overwritten_not_appended(db):
    now = datetime.now(timezone.utc)
    db.add(HealthResult(component="duffel", status="ok", checked_at=now))
    db.commit()
    # a second check for the same component overwrites (PK = component), not appends
    row = db.get(HealthResult, "duffel")
    row.status = "down"
    row.fault_code = "401"
    db.commit()
    rows = db.query(HealthResult).filter(HealthResult.component == "duffel").all()
    assert len(rows) == 1 and rows[0].status == "down"
