# Design note — Test architecture: functional vs. user tests, results in Postgres

**Status:** Design decision for review. Folds into the Admin Guide (Ch. 21
Testing & UAT) and the self-health-loop philosophy (deterministic state in
Postgres, visible in the app).

---

## Why this note exists

Two things surfaced together:
1. The hand-maintained test plan (`docs/jarvis-test-plan.md`) is already 40+
   tests stale — a markdown snapshot drifts the moment anyone writes a test.
2. This week, **233 green pytest tests coexisted with a silently-failing morning
   brief, dead calendar auth, and a non-reporting Tasker.** Unit-green told us
   nothing about whether the system worked in hand.

Both point at the same structural fix: split the two kinds of testing that were
being conflated, and make results *generated*, not *transcribed*.

---

## 1. Two test suites, different questions

### Functional tests (pytest — exists, 233 tests)
- **Question:** is the *code* correct in isolation?
- **Environment:** isolated SQLite per test, everything external stubbed/
  monkeypatched, no live calls.
- **When:** every commit, in CI, before deploy.
- **Owns:** logic, gating, edge cases, regression against fixed prod incidents.
- **Blind spot (by design):** says nothing about real auth, real APIs, real
  Twilio, or whether the deployed system actually works.

### User tests (live scripts — NEW)
- **Question:** does the *system* work end-to-end, right now, deployed?
- **Environment:** the live app, real auth, real external services.
- **When:** run by the admin (Matt) during UAT — after a deploy, after an auth
  fix, or on a schedule.
- **Owns:** the per-component "does it actually work" walkthrough. These are the
  Admin Guide's per-chapter **UAT checks** promoted into runnable scripts.
- **Catches exactly this week's failures:** a user test for `scheduling` would
  have failed on the dead calendar auth while every pytest stayed green.

> The two are complementary, not redundant. Functional proves the code; user
> proves the deployment. You need both, and conflating them is why UAT has been
> scattershot — there was no named home for the live, end-to-end pass.

---

## 2. Test results in Postgres — YES (Idea A)

Store every test *run's results* in Postgres so the web app can show current and
historical status.

`test_run` (one row per suite execution):
| column | type | notes |
|---|---|---|
| `id` | pk | |
| `suite` | str | `functional` \| `user` |
| `commit` | str | git sha under test |
| `started_at` / `finished_at` | datetime | |
| `total` / `passed` / `failed` / `skipped` | int | |
| `source` | str | `ci` \| `local` \| `manual` |

`test_result` (one row per test per run):
| column | type | notes |
|---|---|---|
| `run_id` | fk→test_run | |
| `name` | str | test function / script name |
| `component` | str | maps to the health-model `component` (join key) |
| `status` | str | `pass` \| `fail` \| `skip` |
| `duration_ms` | int | |
| `detail` | text | failure message if any |

**Why this is the right shape:**
- Kills the staleness problem permanently — the "test plan" becomes a *live
  query*, not a hand-copied markdown file. Idea born from the 40-test drift.
- Same philosophy as the health loop: deterministic state in Postgres, visible
  in Admin. The `component` column lets test status join the topology — "which
  agent's tests are failing" sits right next to "which agent's APIs are down."
- Machine-readable: pytest emits JUnit XML / JSON (`--junitxml` or
  `pytest-json-report`); a small writer parses it and inserts rows. User-test
  scripts write their own rows directly.

---

## 3. Invoking tests FROM the web app — NO (Idea B), route via CI instead

The tempting version — an endpoint that runs the suite on demand — is a real
liability and should not be built:
- The suite spins up DBs, monkeypatches, and exercises destructive paths. Running
  that in/near the production process risks contaminating prod state or
  exhausting the 512MB VM.
- An HTTP endpoint that executes the test runner is an arbitrary-code-execution
  surface on an internet-facing app that can book flights and send email. That
  must not exist.
- It inverts the trust model: tests gate deploys from a *controlled* environment;
  the deployed artifact should not run test code on itself.

**The honest version of what "invoke on demand" wants:**
- **Functional:** trigger a CI run (GitHub Actions `workflow_dispatch`). CI runs
  the suite in a clean runner and writes results to Postgres. The web app shows
  the results and can *link to* a manual trigger — it never executes tests
  itself.
- **User:** these ARE meant to run against the live app, but as scripts the admin
  runs (or a scheduled CI job runs), writing their results to `test_result`.
  Still not the production process shelling out to a runner on an HTTP request.

Net: **read/display test status in the app: yes. Execute tests from the app
process: no — that goes through CI.**

---

## 4. CI gap to fix (found while scoping this)

Current `.github/workflows/fly-deploy.yml` **deploys on every push to main
without running the tests first.** A red suite does not stop a deploy today.

Fix, which also lands the results-in-Postgres pipe:
1. Add a `test` job that runs before `deploy`:
   - `pytest --junitxml=results.xml` (functional).
   - Parse + write a `test_run` + `test_result` rows to Postgres.
   - `deploy` job `needs: test` — a red suite blocks the deploy.
2. Add a `workflow_dispatch` trigger so the suite can be re-run on demand from
   GitHub (the safe version of "invoke the tests").
3. (Later) a scheduled workflow that runs the **user** tests against the live app
   and writes their results too — turning "does it actually work" into a
   monitored, historical signal instead of a manual poke.

---

## 5. How this shows up in the Admin Guide

- **Ch. 21 (Testing & UAT)** documents both suites, how to run each, and the
  functional/user distinction.
- Each **Part II component chapter's UAT checks** become the source material for
  that component's **user-test script**.
- The Admin page gains a **test-status view** (from `test_run`/`test_result`),
  sitting alongside the health-status view — same page, same philosophy: the
  app shows you its own state.

---

## 6. Sequencing

This is NOT urgent relative to PR-1 → Tasker → Admin Guide. But it slots cleanly:
- **After the Admin Guide chapters exist** (they define the UAT checks that become
  user tests).
- **Alongside or after the health-model PR** (shares the `component` join key and
  the "deterministic state in Postgres, visible in app" pattern).
- The **CI test-gating fix (§4.1)** is worth doing sooner on its own merit —
  it's small and closes a real hole (deploys not gated on green tests),
  independent of the Postgres storage.
