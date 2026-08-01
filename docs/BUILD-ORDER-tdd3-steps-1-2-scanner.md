# BUILD ORDER — TDD #3, Steps 1–2: `github_write_log` + secret scanner

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-repo-scaffolding.md` (§4.5, §5, §8, §9)
**Type:** Code PR. **Merge-on-green authorized** for the code once CI is green —
these two steps introduce no write path and no outward-facing switch. Nothing
here is gated.
**Scope discipline:** Steps 1–2 ONLY. Do **not** build `commit_document` (step 3),
`create_project_repo` (step 5), or touch visibility defaults in this PR. The
scanner is deliberately built and tested *before any writer exists* — that
ordering is the point (§8).

---

## Why these two, now

The secret scanner is the one protective component in the whole project arc — it
reduces live exposure risk the moment it lands, independent of the interview
engine. TDD #3 §8 requires it to exist *before* any write path, and a later
ratified decision (public-by-default for inception repos) promoted "scanner
precedes any public write" from prudence into a **safety property**. Building it
first satisfies that ordering by construction.

---

## Step 0 — Confirm the migration slot against live head (do this first, always)

The draft TDDs were written when the numbers were different. Do not trust them.

```
cd backend && alembic heads
```

Expected single head: `0025_capability_rollup`. If so, this migration is
**`0026_github_write_log`**, `down_revision = "0025_capability_rollup"`. If head
is anything else, use the real next number and report what you found before
proceeding — a fork or an unexpected head is a stop-and-report, not a guess.

**Downstream note to carry forward (not this PR's work):** whatever number this
consumes, the *next* orders rebase off it — TDD #2 (planning sessions) and
inception both currently write "0026" in their drafts and are now stale. Each
rebuilds its migration number off live head at its own build time. State this in
the PR description so it's on the record.

---

## Step 1 — Migration `0026_github_write_log` + model

One new table. TDD #3 §5.

### 1.1 Model — `GithubWriteLog` in `models.py`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `operation` | str(16) | `create_repo` / `commit_doc` / `open_pr` |
| `target` | str(400) | repo full-name or repo/path — where the write went |
| `ref` | str(400), default "" | branch/PR ref when applicable |
| `ok` | bool | write succeeded |
| `error` | text, default "" | failure detail when `ok=false`. **Never a secret** — see 2.4 |
| `created_at` | timestamptz | `server_default=func.now()` |

The write log exists so a failed or partial write is diagnosable after the fact
(§5). It is written by the *writers* (steps 3/5) — not by this PR — but the table
and model land now so those steps have somewhere to write.

### 1.2 Migration

Follow the shipped `0024_projects` dialect: `Table.__table__.create(bind=...,
checkfirst=True)` in `upgrade`, drop in `downgrade`. `revision =
"0026_github_write_log"`, `down_revision` = confirmed head from Step 0.

### 1.3 Test

- `test_github_write_log_migration_roundtrips` — upgrade then downgrade clean on a
  fresh DB (mirror the existing migration tests' pattern).
- `test_github_write_log_model_defaults` — `error` defaults empty, `created_at`
  auto-set.

---

## Step 2 — The secret scanner (standalone, no writer)

**This is the whole point of the PR.** A pure function with no I/O, no GitHub
client, no write path. It takes text, returns findings. TDD #3 §4.5.

### 2.1 Where it lives

New module, `backend/app/secretscan.py` (standalone — not under `handlers/`, it's
not a tool). A single public entry:

```python
def scan_for_secrets(text: str) -> list[SecretFinding]:
    """Return findings; empty list == clean. No I/O, no logging of matches."""
```

`SecretFinding`: `pattern_name`, `line` (1-based), `char_span` — **never the
matched value** (2.4).

### 2.2 What it catches (§4.5)

- **Known token prefixes**, each a named pattern so a hit reports *which*:
  `ghp_`, `github_pat_`, `duffel_`, `sk-ant-`, `xoxb-`, `AIza`, and Twilio SID
  (`AC` + 32 hex). One test per prefix (§9).
- **Private key headers** — `-----BEGIN … PRIVATE KEY-----`.
- **High-entropy strings** above a length threshold — Shannon entropy over a
  configurable floor AND minimum length, to avoid flagging ordinary prose. Start
  conservative; §11 (TDD) says tune from real refusals, not imagination. Make the
  threshold a module constant now, a setting later if it proves noisy.
- **(Defer within this step, note only):** "values matching known Fly secret
  names if resolvable" (§4.5 bullet 3) requires reading the live secret
  environment — that's a real I/O dependency and a judgment call about whether the
  scanner should ever hold secret *values* in memory to compare against. **Do not
  build it in this PR.** Leave a `# TODO(§4.5): value-match against known secrets
  — needs a decision, see TDD open questions` and report it back. Prefix + entropy
  + key-header covers the exposure that matters for a first cut.

### 2.3 What it must NOT do

- No network. No file reads. No logging of input text or matched values. It is a
  pure classifier so it can be called anywhere without side effects and tested
  offline.
- It does not decide what to do about a hit — it reports. The *writer* (step 3,
  later) aborts on a non-empty result. Keeping detection and enforcement separate
  means the scanner is trivially testable and the abort is asserted at the call
  site later.

### 2.4 The non-echo invariant — assert it, don't trust it

A scanner that leaks the secret into its own finding, log line, or error message
has reintroduced the exposure it exists to prevent. This is the sharpest test in
the set:

- `test_finding_never_contains_matched_value` — scan text with a known
  `sk-ant-…` token; assert the returned findings, their repr, and any log output
  contain the *pattern name and location* but **not** the token substring.
- Mirror TDD #3 §9's "does not echo the secret" — the matched value is absent from
  the finding and (later) from `github_write_log`.

### 2.5 Tests (TDD #3 §9, scoped to what exists in this PR)

- **One test per prefix** — each known token type is caught (`ghp_`, `duffel_`,
  `sk-ant-`, Twilio SID, `xoxb-`, `AIza`, `github_pat_`).
- **Private key header caught.**
- **Non-echo** (2.4) — the load-bearing one.
- **Clean text passes** — a normal design document with prose and code fences
  returns no findings. Guard against the scanner being uselessly trigger-happy.
- **Entropy floor is conservative** — a base64-looking but innocuous string
  (e.g. a short hash in a doc) does not trip it at the starting threshold; a real
  40-char high-entropy secret does. This encodes the false-positive caution as a
  test rather than a hope.

---

## Guardrails

- **Steps 1–2 only.** No writer, no repo creation, no visibility change. If the
  work starts wanting a GitHub client, stop — that's step 3, a different order.
- **Living-document rule (CLAUDE.md):** this adds a table and a module. Update
  `docs/ARCHITECTURE.md` in the same PR — the `github_write_log` row in the table
  inventory and a line for `secretscan.py`. Don't bump "Last full audit."
- **Registry discipline:** the scanner is not a registered tool and takes no
  audit row — it's an internal function, not a component with liveness. That's
  correct; don't wire it into the health model.
- **Run the suites before pushing:** `python -m pytest -q` from `backend/`, and
  the `ui-test` job is untouched here (no frontend change).

## Report back on completion

- The confirmed migration number from Step 0 (and thus what #2/inception must
  rebase to).
- That the non-echo test (2.4) is present and green — call it out specifically.
- The starting entropy threshold chosen, so it's on record for later tuning.
- Confirmation the `# TODO(§4.5)` value-match deferral is filed, not silently
  dropped.

Merge-on-green once CI is green and ARCHITECTURE.md is updated in the same PR.
