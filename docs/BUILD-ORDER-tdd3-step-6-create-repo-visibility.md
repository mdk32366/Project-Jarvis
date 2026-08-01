# BUILD ORDER — TDD #3, Step 6: `create_project_repo` + visibility flip (GATED)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-repo-scaffolding.md` (§4.1, §4.3, §6.2)
**Builds on:** #53 (scanner), #54 (`commit_document` enforcing), #55 (scaffold renderer)
**Type:** Code PR. This step creates repos and changes outward-facing visibility
behavior, so it is **NOT plain merge-on-green**. The tool is **gated**; the
visibility-default change is an outward-facing behavior change. Merge the code on
green, but the decisions below are already ratified — do not re-open them, and do
not extend them past what's written.

---

## The ratified decisions this step implements (do not re-litigate)

- **Create public, uniformly.** Both the new `create_project_repo` and the
  existing `create_project_from_idea` create **public** repos by default.
- **`create_project_repo` is gated** — confirmation readback of name + visibility
  + owner before anything is created (§6.2).
- **`create_project_from_idea`'s default flips** from `private=True` to
  `public`. Its existing gated readback (`_summarize_promote`) already renders
  visibility, so the flip surfaces in the confirmation automatically — verify it
  reads "public" after the change.
- **Scanner precedes every write** — the scaffold and any seeded content pass
  through `scan_for_secrets` before the GitHub PUT. This is the safety
  precondition that made uniform-public acceptable; it is now satisfied because
  the scanner exists. **The scanner and the public default may not be separated.**
- **Go-private is owner action**, prompted by the project close-out. JARVIS never
  flips an existing repo's visibility. `visibility_review` (auto-surfacing dormant
  public repos) is parked and named — **not built here.**

---

## Step 0 — Confirm the reconciliation points against live bytes

Already read by the Planner, but re-confirm before editing:

- `handlers/ideas.py::_create_project_from_idea` — `private = bool(args.get("private", True))`. This default flips.
- `handlers/ideas.py::_summarize_promote` — already renders `"private" if ... else "public"`. Confirms the readback surfaces the flip.
- The seed loop currently hardcodes `README.md` + `docs/idea.md` and PUTs to the default branch with **no scanner and no scaffold**. Step 6 routes it through both.
- `app/scaffold.py::render_scaffold` (from #55) — confirm its real signature; the new path calls it.

Report any drift from the above before proceeding.

---

## Step 1 — `create_project_repo(project, name, visibility='public')` — new, gated

TDD §6.2. Creates a repo, seeds the **scaffold** (not README+idea — that's the
idea path), records `repo_url`, logs.

### 1.1 The pipeline

1. **Scaffold render + scan.** `render_scaffold(name, description)` → the file
   set. **Scan every file** (`scan_for_secrets`) before any GitHub call. Any
   finding aborts before the repo is created — no repo, no partial state. (The
   scaffold is static so a finding is unlikely, but the scan is the invariant, not
   an optimization — a public repo write without a scan is the exact hazard the
   ratified ordering forbids.)
2. **Create the repo** — `POST /user/repos`, `private=False` for
   `visibility='public'`. Reuse the `_API` / `_github_headers` pattern.
3. **Seed the scaffold** — PUT each rendered file (first PUT creates the default
   branch, as the idea path already relies on).
4. **Set `project.repo_url`** on the passed project.
5. **Log** to `github_write_log` — `create_repo` op, `ok`, `ref`, `error` (never a
   scanner value — the non-echo invariant, doubly so because the log renders on
   the status page).

### 1.2 Gate

Gated like `create_project_from_idea` — `register_gated`, top-level only,
sub-agents refuse. Readback (`summarize`) states **name, visibility, and owner**:
"create a new **public** GitHub repo '<name>' under <owner>". Visibility in the
readback is non-negotiable — it is the owner's chance to catch an unwanted public
before it exists.

### 1.3 Idempotence (§6.2)

If the repo already exists: **do not fail destructively, do not overwrite the
scaffold.** Report it, set `repo_url` if unset, stop. A half-created repo from a
network failure must be recoverable by re-running.

---

## Step 2 — Flip `create_project_from_idea` to public + route through scanner

The existing path keeps working; two changes:

1. **Default flip.** `private = bool(args.get("private", False))` — public by
   default. An explicit `private=true` from the caller still honored (the owner
   can override in a specific case), but the default is public. Confirm
   `_summarize_promote` now reads "public" in the readback (it will, unchanged —
   it reads the same arg).
2. **Scanner on the seeded content.** The README + idea markdown it PUTs are now
   **public at the moment of commit**. Scan them (`scan_for_secrets`) before the
   PUT — an idea body captured from SMS/voice could contain a pasted token. A
   finding aborts the promotion with the pattern name + location, never the value.
   This closes the §11.3 consent concern's *secret-exposure* axis: idea bodies go
   public, but never with a secret in them.

**Do not** change the idea path's seed *content* (still README + idea.md) or its
gate — only the visibility default and the pre-PUT scan.

---

## Step 3 — Tests

- **`create_project_repo` is gated** — an ungated/sub-agent invocation refuses;
  assert nothing created.
- **Readback states visibility** — the confirmation text contains "public" (or the
  requested visibility) + name + owner.
- **Scanner precedes creation** — a scaffold/seed containing a token → **no
  `POST /user/repos` call at all** (assert the client is never constructed for the
  create), abort with pattern name, no value echoed, `github_write_log` records
  the block without the value.
- **Public by default, both paths** — `create_project_repo` with no visibility arg
  → public; `create_project_from_idea` with no `private` arg → public. Both
  assert `private=False` reaches the GitHub payload.
- **Explicit private still honored on the idea path** — `private=true` → private
  repo (the override still works).
- **Idempotent repo creation** — existing repo → reports, sets `repo_url`, does
  not overwrite scaffold, no destructive call.
- **Idea-body scan blocks a tokened promotion** — an idea whose body contains
  `sk-ant-…` → promotion aborts, no repo, value not echoed.
- **`repo_url` recorded** — successful creation sets `project.repo_url`.
- **Never flips existing visibility** — assert no code path in this PR changes an
  existing repo's public/private state. JARVIS creates; she does not flip.

---

## Guardrails

- **Scanner-and-public are inseparable.** If any change would let a public repo or
  a public seed commit happen without a preceding scan, stop — that violates the
  ratified precondition. This is the one constraint in the arc that is a safety
  property, not a preference.
- **`visibility_review` is NOT in scope.** Auto-surfacing dormant public repos is
  parked. Do not build it, do not stub it.
- **Go-private stays owner action.** No code here flips an existing repo private.
  The close-out reminder is the mechanism; it is a process step, not code in this
  PR.
- **Living-document rule:** adds `create_project_repo`, changes visibility
  behavior on two tools. Update `docs/ARCHITECTURE.md` in the same PR — tool
  inventory, the channel/gate section, and note the public-default + scanner
  precondition. Don't bump "Last full audit."
- **Registry discipline:** both tools run through `Registry.run_tool`; the gate
  runs in `orchestrator.run`. Don't special-case.
- **Run both suites before pushing.**

## Report back

- Confirmation the visibility default flipped on **both** paths, asserted in test.
- That "scanner precedes creation — client never constructed on a finding" is
  present and green (the safety-precondition proof). Call it out.
- That `create_project_from_idea`'s readback now reads "public".
- That no path flips an existing repo's visibility (asserted).
- Migration head unchanged at `0026_github_write_log` (this step adds none — if
  you're writing a migration, stop and report why).

Merge the code on green; the visibility flip is ratified, so green + ARCHITECTURE
update is the merge bar. Step 7 (github health component) follows.
