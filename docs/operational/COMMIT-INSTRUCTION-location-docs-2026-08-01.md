# COMMIT INSTRUCTION — location docs to `docs/`

**For:** Builder (Claude Code, local repo)
**Type:** **Docs-only commit. NOT an execution.** Add these files to `docs/`.
Do **not** build, implement, or act on anything they describe. Committing the
files *is* the whole task.

## What to do

Place these three files into `docs/` in the local repo and commit them:

1. `TDD-location-freshness-alert.md` → `docs/TDD-location-freshness-alert.md`
2. `design-note-answering-late.md` → `docs/design-note-answering-late.md`
3. `BUILD-ORDER-location-pull-silence-diagnostic.md` → `docs/BUILD-ORDER-location-pull-silence-diagnostic.md`

## Rules

- **Docs-only, straight to `main`** — per CLAUDE.md, docs-only commits to `main`
  are allowed and skip CI deploy. This is that.
- **No code, no migration, no tool, no build.** If you find yourself editing
  anything under `backend/` or `ui/`, stop — that's not this task.
- **No ARCHITECTURE.md change required** — these are design records, not a
  structural change to the running system. (The freshness feature is not built;
  when it is, that build's PR updates ARCHITECTURE.md.)
- One commit is fine; message something like:
  `docs: stage location freshness TDD + answering-late note + pull-silence diagnostic`

## Why these three, briefly (context, not instruction)

- **TDD-location-freshness-alert.md** — the design for the freshness check +
  absence watch. Planner-ready, NOT build-ready (its own status line says so).
  Carries the `answering_late` revision.
- **design-note-answering-late.md** — the standalone finding: late-is-not-silent,
  raising-the-timeout-is-fabricated-green, the evidence-gets-discarded lesson.
- **BUILD-ORDER-location-pull-silence-diagnostic.md** — the tabled 6h-gap
  diagnostic order; the paper trail for how the answering_late finding was reached.

These live only in a chat stream right now. Committing them is what stops them
becoming the next lost-context casualty (as the inception TDD was earlier today).

## Report back

- The three files are in `docs/` and committed to `main`.
- No `backend/`/`ui/` files were touched.
- Confirm you did **not** build anything — this was a docs commit only.
