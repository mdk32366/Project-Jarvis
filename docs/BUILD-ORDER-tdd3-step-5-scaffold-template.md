# BUILD ORDER — TDD #3, Step 5: the scaffold template (stored, versioned)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-repo-scaffolding.md` (§4.4)
**Builds on:** PR #53 (scanner), PR #54 (`commit_document` enforcing)
**Type:** Code PR. **Merge-on-green authorized.** No decisions in this step — it is
pure versioned-template plumbing and a prerequisite for step 6 (a repo you create
needs something to scaffold *with*). No GitHub writes, no repo creation, no
visibility anything in this PR.
**Scope discipline:** Step 5 ONLY. This PR produces the *template and the function
that renders it into a set of files*. It does **not** create a repo, does **not**
commit anything to GitHub, does **not** touch `create_project_from_idea`. Those
are step 6, which is decided and comes next as its own order. If this PR starts
wanting a GitHub client, stop.

---

## What step 5 is

TDD §4.4: a **stored, versioned template** in `Project-Jarvis` for the standard
new-project structure — *not* reconstructed from the model's memory per
invocation. The TDD's own reasoning is the whole point:

> A structure regenerated from memory each time will drift, and drift in the thing
> whose entire job is preventing drift is a special kind of failure.

So this step commits the template *as files in the repo* and writes a pure
function that reads/renders them into the file set a new repo will be seeded with.
Deterministic, offline-testable, no I/O beyond reading its own template files.

---

## The scaffold structure (§4.4)

```
README.md
ARCHITECTURE.md
docs/
  README.md          ← carries the convention itself (the important file)
  archive/.gitkeep
  operational/.gitkeep
.gitignore
```

`docs/README.md` is the load-bearing file. It carries the tier convention
verbatim, including the organizing principle (§4.4):

> Files are sorted by whether they are **live**, **superseded**, or **spent** —
> not by topic. `docs/` holds live design records and active references.
> `docs/archive/` holds superseded documents, each with a banner naming what
> replaced it. `docs/operational/` holds executed handoffs and checklists.
>
> Commit the design before the work is done.

---

## Implementation

### 1. Where the template lives

Store the template files under a versioned path in `Project-Jarvis` — e.g.
`backend/app/scaffold/template/` — as real files, tracked in git. This is what
makes it versioned: a change to the scaffold is a diff in a PR, reviewable, not a
silent change in a code string. `.gitkeep` files that must ship as *content* in a
created repo need care so they aren't ignored locally — confirm they're tracked
(a `.gitkeep` is conventionally not gitignored; verify against this repo's
`.gitignore`).

Template files that need per-project substitution (project name, date, initial
description) use a simple, explicit placeholder — a named token like
`{{PROJECT_NAME}}`, not f-string interpolation of arbitrary code. The renderer
substitutes known keys only; an unknown placeholder left in output is a test
failure, not a silent passthrough.

### 2. The renderer

New module `backend/app/scaffold.py` (or under a `scaffold/` package):

```python
def render_scaffold(project_name: str, description: str = "",
                    now: date | None = None) -> list[ScaffoldFile]:
    """Read the versioned template, substitute the known placeholders, and
    return the full file set (path + text) a new repo will be seeded with.
    Pure: reads template files, no network, no repo, no DB."""
```

`ScaffoldFile`: `path` (repo-relative), `content` (text). The list is the complete
set of files step 6 will PUT into a new repo — step 5 stops at producing it.

- Substitutes `{{PROJECT_NAME}}`, `{{DESCRIPTION}}`, `{{DATE}}` (and whatever the
  template actually uses — derive the key set from the template, don't hardcode a
  guess).
- Every placeholder in every template file must be satisfied. An unrendered
  `{{...}}` in the output is a bug and a test asserts its absence.
- `docs/README.md`'s convention text has **no** placeholders — it is verbatim,
  the same in every repo. Assert it renders byte-for-byte with the template.

### 3. Why a renderer and not "step 6 writes the files directly"

Because step 6 is gated, outward-facing, and already carries the repo-creation
risk. Keeping scaffold *rendering* (pure, testable, no I/O) separate from scaffold
*committing* (GitHub, in step 6) means the structure is fully proven offline
before a single byte hits a real repo. Same split as scanner (detect) vs.
commit_document (enforce): the risky half calls a proven pure half.

---

## Tests (TDD §9 "scaffold completeness", scoped to step 5)

- **All files present** — `render_scaffold(...)` returns every path from §4.4:
  `README.md`, `ARCHITECTURE.md`, `docs/README.md`, `docs/archive/.gitkeep`,
  `docs/operational/.gitkeep`, `.gitignore`. Assert the full set, by path.
- **`docs/README.md` carries the convention verbatim** — assert the tier-convention
  text (live/archive/operational + "Commit the design before the work is done")
  is present and byte-identical to the template. This is the anti-drift guarantee
  in test form.
- **Placeholders are fully substituted** — no `{{...}}` token survives in any
  rendered file. A project named "Foo" produces a README naming Foo, no literal
  `{{PROJECT_NAME}}` anywhere.
- **Unknown placeholder fails loudly** — if a template file contains a placeholder
  the renderer doesn't know, `render_scaffold` raises rather than emitting it. (A
  silent unrendered token in a real repo is the drift the whole step exists to
  prevent.)
- **Deterministic** — same inputs (including a fixed `now`) produce byte-identical
  output across calls. No embedded wall-clock except through the injectable `now`.
- **Template is read from disk, not a code string** — assert the renderer reads
  the versioned template files (so editing the template changes the output),
  rather than carrying the structure inline. This pins §4.4's "stored, not
  regenerated from memory."

---

## Guardrails

- **Step 5 only.** No GitHub client, no repo creation, no `create_project_from_idea`
  changes, no visibility defaults. Pure render, offline-tested.
- **Living-document rule (CLAUDE.md):** adds a module and a tracked template
  directory. Update `docs/ARCHITECTURE.md` in the same PR — note `scaffold.py`
  and the template location. Don't bump "Last full audit."
- **No migration** — this step touches no schema. If you find yourself writing
  one, stop and report why.
- **Run both suites before pushing:** `python -m pytest -q` from `backend/`;
  `ui-test` untouched.

## Report back

- The full path list `render_scaffold` produces, confirmed against §4.4.
- That the `docs/README.md` verbatim-convention test is present and green (the
  anti-drift guarantee).
- That the template reads from tracked files on disk, not an inline string —
  and that `.gitkeep` files are actually tracked, not locally ignored.
- Migration head unchanged at `0026_github_write_log`.

Merge-on-green once CI is green and ARCHITECTURE.md is updated in the same PR.

---

## Carried forward to the step 6 order (NOT this PR — recorded so it's not lost)

Step 6 is **decided** and comes next as its own build order. The ratified
decisions, pinned here so they're on the record when that order is written:

- **Create public, uniformly** — both `create_project_repo` (new) and the existing
  `create_project_from_idea` (which currently defaults `private=true` in its schema
  and `_summarize_promote` — that default **flips to public** in step 6).
- **`create_project_repo` is gated** — readback of name + visibility + owner
  before creation. The visibility change to the idea-promotion path surfaces in
  *its* existing gated readback, which is exactly where a visibility change should
  be visible to the owner.
- **Scanner-precedes-flip is satisfied** — the scanner exists and enforces, so
  uniform-public no longer outruns its safety precondition. This step-6 order is
  the moment that constraint was written for.
- **Go-private is owner action, prompted by the project close-out** — JARVIS never
  flips visibility. The close-out reminds the owner to set a repo private until the
  next development spurt. Known upgrade path if that reminder-discipline proves
  leaky: a `visibility_review` exception-line surfacing long-dormant public repos.
  Parked, named, not built.
