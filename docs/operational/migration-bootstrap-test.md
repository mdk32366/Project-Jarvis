# How-to: verify `alembic upgrade head` bootstraps a fresh database

## Why this exists

The pytest suite builds its schema with `Base.metadata.create_all()` on SQLite — it **never
runs the Alembic migration chain**, and never touches Postgres. So a migration that is broken
only against a *fresh Postgres* passes every test and still bricks a real deploy. That is
exactly what audit **H2** was: nine tables existed only because the app calls `create_all()`
at boot, and a later migration did `ALTER TABLE` against a table no migration had created —
fine on the long-lived prod DB, fatal on a rebuild, because Fly runs
`release_command = "alembic upgrade head"` *before* any app process boots.

This test proves the one thing the suite can't: **`alembic upgrade head` alone produces a
complete, working schema on an empty database.**

## The one rule that makes the test valid

> **Never start the app (uvicorn) against the test database before running Alembic.**

The app's lifespan calls `create_all()` at boot, which creates every table from the models —
masking the exact bootstrap bug you're trying to catch. The test DB must see Alembic *first*,
and nothing else.

---

## A. Manual run (local, ~1 minute)

Uses the repo's own `docker-compose.yaml` Postgres. It runs against a throwaway database
(`migration_test`) so your dev `app` database is never touched.

```bash
# 1. Start the local Postgres (if not already up)
docker compose up -d db

# 2. Create a guaranteed-empty throwaway database
docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS migration_test;"
docker compose exec db psql -U postgres -c "CREATE DATABASE migration_test;"

# 3. Run ONLY the migrations against it (do NOT start the app first)
cd backend
DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/migration_test" \
  python -m alembic upgrade head
#    ^ must exit 0. An UndefinedTable / ProgrammingError here is the bug.

# 4. Verify the schema is complete and at head
DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/migration_test" \
  python - <<'PY'
import os, sqlalchemy as sa
e = sa.create_engine(os.environ["DATABASE_URL"])
insp = sa.inspect(e)
tables = set(insp.get_table_names())
must_have = {  # the create_all-only bootstrap tables + a couple of altered ones
    "users", "conversations", "messages", "persona_profile", "preferences",
    "memories", "contacts_whitelist", "actions_audit", "pending_confirmations",
    "tasks", "voice_turns",
}
missing = must_have - tables
assert not missing, f"MISSING TABLES: {missing}"
# altered columns that a broken chain would drop
cols = {c["name"] for c in insp.get_columns("pending_confirmations")}
assert {"code_deadline", "code_attempts"} <= cols, "pending_confirmations columns missing"
assert "google_id" in {c["name"] for c in insp.get_columns("tasks")}
assert "notify_email" in {c["name"] for c in insp.get_columns("voice_turns")}
rev = e.connect().execute(sa.text("select version_num from alembic_version")).scalar()
print(f"OK — {len(tables)} tables, alembic at {rev}")
PY

# 5. Clean up
docker compose exec db psql -U postgres -c "DROP DATABASE migration_test;"
```

Green means a fresh Fly Postgres (or a restored backup) will bootstrap cleanly.

> Note: the SQLite scratch-DB check used during development
> (`DATABASE_URL=sqlite:///scratch.db python -m alembic upgrade head`) proves the revision
> chain is *linked and importable*, but it does **not** exercise the Postgres-only guards
> (the `to_regclass` / dialect branches). Only the Postgres run above does. Use SQLite for a
> quick smoke, Postgres for real confidence.

---

## B. Automated in CI (the real fix — recommended)

Don't rely on remembering to run section A. Add a job that does it on every PR, against a
throwaway Postgres service. This turns "someone should check" into "it's checked, always."

Add to `.github/workflows/fly-deploy.yml`:

```yaml
  migrations:
    name: Migration bootstrap (fresh Postgres)
    runs-on: ubuntu-latest
    services:
      db:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: migration_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
      - name: Install deps
        working-directory: backend
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-dev.txt
      - name: alembic upgrade head on an empty DB
        working-directory: backend
        env:
          DATABASE_URL: postgresql+psycopg2://postgres:postgres@localhost:5432/migration_test
        run: python -m alembic upgrade head
```

Then make the deploy wait on it too, so a bootstrap-breaking migration can never ship:

```yaml
  deploy:
    needs: [test, migrations]   # was: needs: test
```

The service container starts empty and the job never boots the app, so the "Alembic first"
rule holds by construction.

---

## C. Disaster-recovery drill (occasional, manual)

CI proves migrations bootstrap a *schema*. It does not prove you can restore *data*. A couple
of times a year, rehearse the real thing:

1. Take a fresh dump of prod: `fly postgres` backup / `pg_dump`.
2. Restore it into a scratch database.
3. Run `alembic upgrade head` against the restored DB (it should be a no-op if prod is
   already at head — but confirms the chain applies cleanly on real data).
4. Point a local app instance at it and smoke-test a login + one call flow.

This is the only part that stays manual, because it's a rehearsal of judgment (do we have
backups, can we actually stand the app back up), not a schema check.

---

## TL;DR

- **Automate section B** — it's the durable fix and closes the H2 class of bug for good.
- **Section A** is for local/ad-hoc confidence before you rely on DR.
- **Section C** is the only genuinely periodic-manual piece, and it's a restore drill, not a
  migration check.
