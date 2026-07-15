# docs/ — design records & operational history

## What lives here
- Root `docs/`: LIVE design records — TDDs, design notes, guides, and active
  reference material. If it's the current source of truth for a design, it's here.
- `docs/archive/`: SUPERSEDED documents, kept for history. Each carries a banner
  pointing to what replaced it. Never treat these as live.
- `docs/operational/`: executed handoffs, dated checklists, one-off task specs.
  Historical record of what was done, not current design.

## The rule (so docs don't drift behind the thinking)
Any design decision, TDD, or architectural note produced in a planning session
gets committed here BEFORE the work it describes is considered done. A design
that exists only in chat is not durable and does not count as recorded.

## Naming
- TDDs: `TDD-<feature>.md`
- Design notes: `design-note-<topic>.md`
- Guides: `<Name>-Guide.md`
- When a doc supersedes another, move the old one to `archive/` with a banner.