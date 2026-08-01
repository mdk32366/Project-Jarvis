# BUILD ORDER — TDD #3, Steps 3–4: `commit_document` (branch + PR) + scanner enforcement

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-repo-scaffolding.md` (§4.1, §4.5, §6.1, §8 steps 3–4, §9)
**Builds on:** PR #53 (117eea2) — `secretscan.py` + `github_write_log` shipped
**Type:** Code PR. **Merge-on-green authorized** for the code once CI is green.
`commit_document` is **ungated** (branch + PR is reversible; consistent with the
existing ideas commit path — §4.1). No gated tool, no repo creation, no
visibility switch in this PR.
**Scope discipline:** Steps 3–4 ONLY. Do **not** build `create_project_repo`
(step 5), the scaffold template (step 4 of the TDD's own numbering — see note
below), the health check (step 6), or touch visibility defaults. This PR turns
the scanner from *detection* into *refusal*, against `Project-Jarvis/docs/` only.

> **Numbering note:** the TDD's §8 build table and this order both say "steps
> 3–4," but they enumerate differently. This order's step 3 = `commit_document`
> (TDD §8 row 3). This order's step 4 = wiring the scanner in as the pre-write
> abort, which the TDD folds into §6.1 step 1 rather than giving its own row. The
> scaffold template (TDD §8 row 4) is **not** in this PR — it belongs with repo
> creation. If anything here starts needing a scaffold or a second GitHub token,
> stop; that's the next order.

---

## Step 0 — Read the shipped scanner's real signature first

This is a "what does the code currently do" question, so read it — do not build
against the signature I sketched in the last order, build against the bytes.

```
sed -n '1,80p' backend/app/secretscan.py
```

Confirm and report:
- The exact public entry point and its return type (I specified
  `scan_for_secrets(text) -> list[SecretFinding]`, but the shipped form is
  ground truth).
- The `SecretFinding` field names (I need the enforcement code to render a
  refusal message from `pattern_name` + `line` **without** touching any
  value-bearing field — 2.3 below).

If the shipped interface differs from what the enforcement code below assumes,
adapt the call site to the real interface and note the delta. The scanner is
correct; the writer conforms to it.

---

## Step 3 — `commit_document(project, kind, tier, title, body)` — branch + PR

TDD §6.1. Ungated. Targets `Project-Jarvis/docs/` in this PR (the only resolvable
destination until `create_project_repo` exists — a `new_project` target with a
null `repo_url` **aborts**, §6.1 step 2).

### 3.1 The ordered pipeline (§6.1) — order is load-bearing

1. **Scan first (§4.5).** `scan_for_secrets(body)` — and title. **Non-empty
   findings abort before any GitHub call.** This is the whole point of the PR:
   detection becomes refusal here. Assert (step 4) that the client is never
   constructed when a finding fires, not merely that it returns early.
2. **Resolve destination repo.** `Project-Jarvis` when the project is JARVIS
   herself; otherwise `project.repo_url`. **Abort if unresolvable — never guess a
   repo** (§6.1 step 2). In this PR, only the `Project-Jarvis` path is reachable;
   a null `repo_url` is the abort test, not a live path.
3. **Resolve path from tier, not from a caller-supplied path.** `live`→`docs/`,
   `archive`→`docs/archive/`, `operational`→`docs/operational/`. The caller
   supplies `tier`; the function derives the path. A caller-supplied path is
   ignored (§6.1 — this is the convention-enforcement point).
4. **Branch** `docs/<slug>-<yyyymmdd>` off the default branch. Reuse `_slug` from
   `ideas.py`. Creating the branch = get the base ref's sha, create a new ref.
5. **Commit** the file to that branch (Contents API PUT with `branch=<the new
   branch>` — reuse the base64 + sha-fetch-before-update idempotence from
   `commit_idea_to_repo`, §the existing pattern).
6. **Open a PR** from the branch to the default branch. **Never merge** (§3, §9).
   There is no merge call anywhere on this path — assert its absence (step 4).
7. **`attach_document(...)`** — this tool already exists in
   `handlers/projects.py` (`_attach_document`, registered). Reuse it; do not
   write a second ProjectDocument insert. Pass the resolved `kind`, `tier`,
   `title`, repo-relative `path`, and the PR `url`.
8. **Log to `github_write_log`** — one `commit_doc` row and one `open_pr` row (or
   a single row per operation as the model's `operation` enum intends), `ok`
   true/false, `ref` = branch/PR ref, `error` empty on success. **The error field
   must never carry a scanner finding's value** — on a scan abort, the log records
   *that* a scan blocked it and the pattern name, never the matched substring
   (§4.5 non-echo, and it's doubly important here because the log renders on the
   status page — the same leaks-twice surface #53 guarded).

### 3.2 Reuse, don't reinvent

- `_API`, `_TIMEOUT`, GitHub headers, base64 PUT, sha-fetch-before-update: all in
  `handlers/ideas.py` (`commit_idea_to_repo`). Lift the pattern; factor shared
  helpers if it's clean, but a little duplication is fine — do not refactor the
  ideas path in this PR (keep the blast radius on the new tool).
- **Key difference from the ideas path:** `commit_idea_to_repo` PUTs straight to a
  branch with no PR. `commit_document` is branch **+ PR, never main**. That's new
  work — the PR-open call and the never-merge invariant don't exist in the ideas
  path. Don't assume the reuse covers it.

### 3.3 Registration

- Ungated, top-level and/or on an appropriate agent roster per how `attach_document`
  is exposed (read its registration and match — likely the same roster).
- **Not reachable from voice** unless justified against `VOICE_TOOLS_PHASE1`
  (CLAUDE.md). A document-commit tool has no business on a spoofable channel —
  leave it off the voice allowlist and say so in the PR.

---

## Step 4 — The enforcement tests (this is what the PR is *for*)

TDD §9. The scanner existed in #53 with no caller; these tests prove the caller
refuses.

- **Scanner blocks the commit — no API call at all.** A `body` containing a token
  (`sk-ant-…`, `ghp_…`) → assert the GitHub client is **never invoked** (patch it
  and assert zero calls), not merely that the function returned an error. §9's
  sharpest line: "assert the client was never invoked."
- **The abort does not echo the secret** — the refusal message and the
  `github_write_log` row contain the pattern name and location, never the matched
  value. Mirrors #53's non-echo test at the enforcement layer, where the value is
  now bound for a stored+rendered log.
- **Tier → path.** `live`→`docs/`, `archive`→`docs/archive/`,
  `operational`→`docs/operational/`. A caller-supplied `path` argument (if the
  schema even allows one — prefer it doesn't) is ignored.
- **Never targets `main`.** Assert every commit is on a `docs/<slug>-<date>`
  branch; assert the PR base is default branch and the head is that branch.
- **Never merges.** Assert no merge endpoint is called on the path. §9, asserted
  not trusted.
- **Unresolvable repo aborts.** A project with null `repo_url` on a `new_project`
  target → abort, no API call, no guess. (The `Project-Jarvis` path stays the only
  live one in this PR.)
- **Clean document commits.** A normal design doc with prose + code fences → scan
  passes, branch created, PR opened, `attach_document` called, two write-log rows.
  The happy path, mocked GitHub.
- **Write-log records failure.** Force a GitHub 422/401 (mock) → `ok=false`,
  `error` populated with the status, no partial ProjectDocument row orphaned.

---

## Guardrails

- **Steps 3–4 only.** No repo creation, no scaffold template, no second token, no
  visibility change. The scanner-precedes-flip safety constraint isn't exercised
  here (no flip in this PR) — but note in the PR that `commit_document` now
  *depends on* the scanner, so the two can never again be separated.
- **Living-document rule (CLAUDE.md):** adds a tool (`commit_document`) and a real
  GitHub write path. Update `docs/ARCHITECTURE.md` in the same PR — the tool
  inventory and any affected diagram. Don't bump "Last full audit."
- **Registry discipline:** `commit_document` is a registered tool and runs through
  `Registry.run_tool` like everything else — the audit-starvation lesson. It gets
  an `actions_audit` row by going through the registry; don't special-case it.
- **Run both suites before pushing:** `python -m pytest -q` from `backend/`;
  `ui-test` is untouched (no frontend change).

## Report back on completion

- The confirmed real `secretscan` signature from Step 0, and whether the
  enforcement call site matched it or had to adapt.
- That the "client never invoked on scan hit" test is present and green — the
  detection-becomes-refusal proof. Call it out specifically.
- That "never merges" is asserted, not assumed.
- Confirmation `attach_document` was reused, not re-implemented.
- Migration head is unchanged (this PR adds no migration — `commit_document` uses
  #53's `github_write_log` and #1's `project_document`; if you find yourself
  writing a migration, stop and report why).

Merge-on-green once CI is green and ARCHITECTURE.md is updated in the same PR.
