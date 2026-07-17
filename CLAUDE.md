# Project JARVIS

Personal voice/SMS/email assistant. FastAPI backend in `backend/`, React SPA in `ui/`,
deployed as Fly app `jarvis-mdk` (api / ingest / worker processes). CI runs pytest on PRs
and deploys to Fly on push to `main`; docs-only pushes skip both.

## Read this first

`docs/ARCHITECTURE.md` is the full system map — orchestrator, agents, tools, gate,
memory tiers, database, jobs, UI. Read it before changing structure.

## Living-document rule

**Any change that alters system structure MUST update `docs/ARCHITECTURE.md` in the same
PR.** That means: adding/removing/renaming a tool, agent, table, channel, job kind, API
route, config flag, or changing gate/confirmation behavior. Update the relevant section
AND any affected Mermaid diagram, and bump the "Last full audit" date only when doing a
full re-verification, not for incremental edits.

## Ground rules

- Work happens in this clone. Branch → PR → merge (never push code directly to `main`;
  docs-only commits to `main` are fine).
- Run tests before pushing: `python -m pytest -q` from `backend/`
  (a working 3.11 venv lives at `../jarvis-app-live/.venv` if this clone has none).
- The confirmation gate is safety-critical. Gated tools (`send_email`, `create_event`
  with attendees, `place_stock_order`, `book_flight`) are registered top-level only;
  sub-agents must refuse them. Never register a gated tool in an agent roster.
- Voice auth is caller-ID (spoofable): anything newly reachable from voice needs to be
  justified against `VOICE_TOOLS_PHASE1` reasoning in `channels/voice_pipeline.py`.
- Design docs live in `docs/TDD-*.md`; significant features get one before code.
