# Claude Code task — CI: gate deploys on green tests

Small, self-contained, closes a real hole: the current pipeline
(`.github/workflows/fly-deploy.yml`) **deploys on every push to main without
running the test suite first.** A red suite cannot stop a deploy today. This adds
a `test` job that must pass before `deploy` runs, plus a manual re-run trigger.

This is deliberately the SAFE version of "run the tests" — tests run in the CI
runner (controlled environment), never in the production app process. (Storing
results in Postgres is a separate, later piece — see
`design-note-test-architecture.md`; not in this task.)

## Facts verified against the repo

- Tests live in `backend/tests`, config in `backend/pytest.ini`
  (`testpaths = tests`, `pythonpath = . tests`).
- Dev deps: `backend/requirements-dev.txt` (`-r requirements.txt` + `pytest`).
- `backend/tests/conftest.py` configures an isolated SQLite DB and stubs ALL
  external services (SMS stub, fake keys). **Tests need no secrets and no
  network** — CI just installs deps and runs pytest.
- Python: 3.11 (matches the project's `.venv`).

## Change

Replace `.github/workflows/fly-deploy.yml` with a two-job workflow: `test` then
`deploy` (deploy `needs: test`). Add `workflow_dispatch` so the suite can be
re-run on demand from the GitHub UI.

```yaml
name: CI / Deploy
on:
  push:
    branches: [main]
  workflow_dispatch:        # manual re-run from the GitHub Actions UI

jobs:
  test:
    name: Run tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        working-directory: backend
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-dev.txt
      - name: Run pytest
        working-directory: backend
        run: pytest -q

  deploy:
    name: Deploy app
    needs: test              # deploy ONLY if tests pass
    runs-on: ubuntu-latest
    concurrency: deploy-group
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

## Notes / gotchas

- `needs: test` is the whole point — it makes `deploy` wait for and require a
  green `test` job. A failing test now blocks the deploy.
- `workflow_dispatch` lets you re-run the suite manually from the Actions tab —
  this is the safe stand-in for "invoke the tests on demand," with zero exposure
  in the production app.
- If any test currently depends on something not in `conftest.py`'s env setup,
  the CI run will surface it — that's a real finding, not a CI bug; fix the test
  isolation, don't paper over it in the workflow.
- Keep `--remote-only` on deploy (unchanged from current).

## Definition of done

- Pushing to main runs `test`, and `deploy` only runs if `test` is green.
- A deliberately-failing test blocks the deploy (verify once, then revert the
  failing test).
- The suite can be re-triggered from the GitHub Actions UI via `workflow_dispatch`.
- No secrets added; the `test` job needs none.
