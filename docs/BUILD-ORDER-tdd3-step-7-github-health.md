# BUILD ORDER — TDD #3, Step 7: `github_writes` health component

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-repo-scaffolding.md` (§7)
**Builds on:** #53 (`github_write_log`), #54–#56 (the writers that populate it)
**Type:** Code PR. **Merge-on-green authorized** — this adds observation, no write
path, no gate, no outward-facing switch.
**Scope:** Step 7 ONLY. This is the last step of TDD #3. It adds a health
component that reads `github_write_log` and reports write health. No new tool, no
migration.

---

## The pinned substrate decision (do not revisit)

The component **reads `github_write_log`, not `actions_audit`** — pinned at #53
and restated at #56. Keying it to `actions_audit` would starve it from birth: the
GitHub writes land their evidence in `github_write_log` by design, and a check
that reads the wrong table sees an empty stream and reports a permanent `unknown`
or a false green. This is the same starvation lesson as `location_responsiveness`
(§ the location work) — the difference is we're pinning the substrate *before* the
check exists, so it cannot be built blind.

---

## Step 0 — Read the seams (confirm before editing)

The health substrate is reference-data-driven; read how a component is added
rather than inventing a path:

- `app/health.py::_COMPONENTS` — the seed inventory. Each entry has `name`,
  `kind`, `depends_on`, `check_type`, `description`. `check_type` is one of
  `liveness | secret_age | published_expiry | heartbeat | freshness | none`.
- `app/health_checks.py::_CHECKS` — maps a `check_type` string to a check
  function. A new check kind is a new entry here + a new function.
- `app/health.py::_REMEDIATIONS` — the `(component, fault_code) → runbook` map.
  The join guard requires **every fault code a check emits has a runbook, and no
  orphan runbooks**. `_RETIRED_REMEDIATIONS` handles renamed codes.
- `app/health.py::run_health_cycle` / `_run_check` — the dispatch loop.

**Decision to make from the read, and report it:** does `github_writes` reuse an
existing `check_type` or need a new one? It doesn't fit `liveness` (that reads
`actions_audit` outcomes of a component's *tools*; GitHub writes aren't
credential-liveness). It's closest to a bespoke check like
`check_location_responsiveness` — a dedicated function reading a specific table.
**Recommend a new `check_type="github_writes"` with a dedicated
`check_github_writes` function**, mirroring how the location checks are bespoke
rather than forced into `liveness`. Confirm against the code and say which you did.

---

## Step 1 — `check_github_writes(db, component)` (§7)

Reads the trailing window of `github_write_log`. Three-tier, consistent with
every other check:

- **`ok`** — no failed writes (`ok=false`) in the trailing window (default 7 days,
  from `check_config`).
- **`degraded`** — at least one `ok=false` in the window. A failed doc commit or
  repo creation is worth surfacing but is **not** a system-down: the app runs
  fine, a write just didn't land.
- **`unknown`** — no writes at all in the window. No evidence is not health (the
  standing rule) — a fresh system that hasn't written anything reports `unknown`,
  never green.
- **never `down`** — §7 is explicit: inability to commit a document is not a
  system fault. Cap severity at `degraded`.

Fault codes it emits (each needs a runbook, §2):

- `write_failed` — one or more `ok=false` rows in the window.
- (optional, if cleanly separable from the log's `operation` field)
  `repo_create_failed` vs `commit_failed` — only split if the log distinguishes
  them and the runbooks genuinely differ. Do **not** invent a distinction the log
  can't support — a fault code with no distinct evidence is the orphan the join
  guard exists to catch. If in doubt, one `write_failed` code.

**Non-echo carries here too:** the check reads `github_write_log.error`, which by
#54's invariant never contains a secret value. Do not have the check surface raw
`error` text that could round-trip a value onto the status page — summarize
(status + operation + target), don't dump.

---

## Step 2 — Component seed + runbook(s)

1. **Add to `_COMPONENTS`:** `{"name": "github_writes", "kind":
   "internal_subsystem", "depends_on": "GITHUB_TOKEN", "check_type":
   "github_writes", "description": "Document commits + repo creation to GitHub."}`
   — adjust `kind`/`depends_on` to match how peers are declared (read the
   neighbors; `internal_subsystem` vs `external_api` is a real choice — the writes
   go *out* to GitHub, so `external_api` depending on `GITHUB_TOKEN` may be the
   truer peer. Pick by analogy to the closest existing entry and say which).
2. **Add to `_REMEDIATIONS`** a runbook for **every** fault code Step 1 emits.
   Content (§7): token validity and scope, rate limit, repo existence, branch
   conflict. The runbook is the "place to start," not a root-cause claim — same
   fact-not-cause discipline as the location runbooks it sits beside.
3. **Register the check_type** in `_CHECKS` if you added a new one.

The seed reconciles (adds/updates text) but never removes — so if you rename a
fault code mid-development, retire the old `(component, fault_code)` in
`_RETIRED_REMEDIATIONS` or it stays joinable forever pointing at a stale runbook.

---

## Step 3 — Tests (§7, and the guards the substrate already enforces)

- **Clean window is `ok`** — only `ok=true` rows in the window → `ok`.
- **A failed write is `degraded`, never `down`** — one `ok=false` row → `degraded`,
  fault `write_failed`. Assert it does not escalate to `down` (§7).
- **No writes is `unknown`, not green** — empty window → `unknown`, no fabricated
  ok on a system that hasn't written.
- **Reads `github_write_log`, not `actions_audit`** — the pinned substrate, as a
  test: seed a failed row in `github_write_log` and assert the check sees it;
  confirm it does not read `actions_audit`. This pins the starvation lesson.
- **Runbook join** — every fault code `check_github_writes` emits has a runbook;
  no orphan runbook. (The existing guard; must pass to build.)
- **Non-echo** — a `github_write_log.error` (already value-free by #54) is
  summarized, not dumped raw onto the check result. Assert the check result
  doesn't round-trip raw error text.
- **Component seeds idempotently** — the reconcile adds `github_writes` once;
  running the seed twice doesn't duplicate.

---

## Guardrails

- **Substrate is `github_write_log`.** Not `actions_audit`. This is the one thing
  that must not drift.
- **Never `down`.** A write failure is `degraded`. Committing a doc is not
  load-bearing for the system running.
- **Living-document rule:** adds a component and a check. Update
  `docs/ARCHITECTURE.md` — the component inventory and health section. This is the
  last step of TDD #3, so it's also the natural place to mark TDD #3 complete in
  the doc. Don't bump "Last full audit" unless you're doing a full re-verify.
- **No migration, no tool, no gate.** If you're writing any of those, stop and
  report why.
- **Run both suites before pushing.**

## Report back

- Which `check_type` you used (new `github_writes` vs. reused) and why.
- Which `kind`/`depends_on` the component got, by analogy to which peer.
- That the "reads `github_write_log` not `actions_audit`" test is present and green
  — the pinned substrate, proven.
- That the runbook join passes for every fault code emitted.
- Migration head unchanged at `0026_github_write_log`.
- **TDD #3 status: complete** — steps 1–7 shipped. This closes the arc's
  repo-scaffolding TDD.

Merge-on-green once CI is green and ARCHITECTURE.md is updated in the same PR.
