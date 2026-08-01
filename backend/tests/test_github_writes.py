"""github_write_log — model + migration (TDD #3 §5, build order steps 1).

The migration roundtrip runs the REAL alembic chain in a subprocess against a
throwaway SQLite file, rather than asserting on the text of the migration file
(the `0012_episodes` pattern). Both have a place, but a text assertion cannot
catch a migration that parses fine and fails to run, and this chain does run on
SQLite end to end. The CI `migrations` job remains the authority for Postgres —
this is the cheap local echo of it, not a replacement.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.models import GithubWriteLog

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run alembic against a throwaway SQLite DB.

    A subprocess with an overridden DATABASE_URL, deliberately: `alembic/env.py`
    reads `settings.database_url` from the cached config singleton at import
    time, so an in-process run would fight that cache and silently migrate the
    test database instead of the throwaway one.
    """
    env = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )


def _version(db_path: Path) -> str:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as c:
            return c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def _has_table(db_path: Path, name: str) -> bool:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return name in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_github_write_log_migration_roundtrips(tmp_path):
    """Fresh DB → upgrade head creates the table; downgrade -1 removes it and
    lands back on the previous revision.

    Testing the DOWNGRADE is the half that matters. An upgrade that works is
    proven every deploy; a downgrade that doesn't is discovered during an
    incident, which is the worst possible time to find out.
    """
    db = tmp_path / "migrate.db"

    up = _alembic(db, "upgrade", "head")
    assert up.returncode == 0, f"upgrade failed:\n{up.stdout}\n{up.stderr}"
    assert _has_table(db, "github_write_log")
    assert _version(db) == "0026_github_write_log"

    down = _alembic(db, "downgrade", "-1")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"
    assert not _has_table(db, "github_write_log"), "downgrade left the table behind"
    assert _version(db) == "0025_capability_rollup"


def test_migration_chains_off_the_confirmed_head():
    """The number was confirmed against `alembic heads` at build time, not taken
    from the TDD — which said 0024, a slot that went to projects.

    Pinned in test because the arc has now had THREE stale migration numbers in
    a row (planning sessions says 0023, inception says 0026, this said 0024).
    """
    mig = BACKEND / "alembic" / "versions" / "0026_github_write_log.py"
    text_ = mig.read_text(encoding="utf-8")
    assert 'revision = "0026_github_write_log"' in text_
    assert 'down_revision = "0025_capability_rollup"' in text_


def test_github_write_log_model_defaults(db):
    """`error` defaults empty and `created_at` is server-set."""
    row = GithubWriteLog(operation="commit_doc", target="mdk32366/Project-Jarvis:docs/x.md")
    db.add(row)
    db.commit()
    db.refresh(row)

    assert row.id is not None
    assert row.error == "", "error must default empty, not None — it is rendered"
    assert row.ref == ""
    assert row.ok is False, "a write is not successful until something says so"
    assert row.created_at is not None


def test_failed_write_records_its_reason(db):
    """The table's whole purpose: a failure is diagnosable after the fact."""
    db.add(GithubWriteLog(
        operation="create_repo", target="mdk32366/new-thing",
        ok=False, error="422: name already exists on this account",
    ))
    db.commit()

    row = db.query(GithubWriteLog).filter_by(ok=False).one()
    assert row.operation == "create_repo"
    assert "already exists" in row.error
