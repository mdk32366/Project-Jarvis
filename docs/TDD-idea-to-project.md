# TDD: idea → project — retrieve an idea and scaffold it into a new GitHub repo

**Status:** **v1 IMPLEMENTED 2026-07-17.** Adds a way to read a captured idea back in
full, and to promote one into a brand-new GitHub repository (README + the idea) behind the
confirmation gate. What shipped vs. deferred is noted per section.

## 1. Problem

Ideas are captured (`capture_idea`) and committed to the fixed `jarvis-ideas` repo, and
`list_ideas` shows their titles — but there was no way to (a) read a specific idea's full
text back, or (b) turn one into a real project. The user wants: *retrieve an idea → name the
project → create a new GitHub repo → drop the idea + a README in it → get the link.*

## 2. What was missing (pre-v1)

`handlers/ideas.py` had only `capture_idea` and `list_ideas` (titles only). No get-by-id,
and the entire GitHub integration was a single Contents-API PUT to the one fixed ideas repo —
**no repo creation**. The GitHub PAT already carries `repo` scope, so the foundation existed.

## 3. Capabilities added

### 3.1 `get_idea(idea_id)` — ungated, on the `secretary` roster
Returns the full title + body + tags of one idea, so the user can review it before promoting
("read me idea #3"). Read-only; no gate.

### 3.2 `create_project_from_idea(idea_id, project_name, private=true, description="")` — GATED, top-level
Creates a new GitHub repo and seeds it, then reports the URL. Because creating a named repo is
an **irreversible, outward-facing act**, it is a gated tool (registered top-level via
`ideas.register_gated`, like `send_email`): readback → explicit "confirm" → execute. Sub-agents
can't run it.

## 4. Flow

1. User: "turn idea #3 into a project." The orchestrator delegates to the secretary to
   `get_idea(3)` if it needs the content, and — per the prompt — **asks for the project name**
   if the user didn't give one (never invents it).
2. Orchestrator calls `create_project_from_idea(idea_id=3, project_name="…")` → the gate reads
   it back ("create a new private GitHub repo 'foo' from idea #3") → user confirms.
3. On confirm: `POST /user/repos` creates the repo, then the Contents API PUTs `README.md`
   (project name + the idea, with a "seeded from JARVIS idea #N" footer) and `docs/idea.md`
   (the raw idea). The idea's `promoted_url` is set. The reply is the repo `html_url`.

## 5. Data model

`Idea.promoted_url` (nullable, migration 0016) records the repo an idea became, so `list_ideas`
can show "→ promoted" and a second promotion is refused. `pending_confirmations` is unchanged.
Migration guarded like 0010 (pending) / 0006 (tasks) as appropriate — `ideas` is created by
migration 0004, so a plain `add_column` is safe.

## 6. GitHub API

- Create: `POST https://api.github.com/user/repos` `{name, private, description, auto_init:false}`.
  Returns `html_url`, `full_name`, `default_branch`.
- Seed: `PUT /repos/{full_name}/contents/README.md` and `/contents/docs/idea.md` (base64,
  first PUT creates the default branch). Same PAT + Contents-API pattern as `commit_idea_to_repo`.
- Errors surfaced in English (name collision `422 name already exists`, bad token `401`, etc.),
  mirroring `explain_duffel_error` / `google_oauth.explain`.

## 7. Defaults & decisions (v1)

- **Private by default** — a promoted idea is a fresh scratch repo, not a public release.
- **Owner** = the PAT's own account (`POST /user/repos`). Org targets deferred.
- **Name** used verbatim as given (GitHub slugifies invalid chars itself); collisions are
  reported, not auto-renamed.
- **Gated** — always confirm (notional None), never a second factor (not money).

## 8. Acceptance criteria (tests, written first)

1. `get_idea` returns the full body; unknown id → a clean "no idea #N".
2. `create_project_from_idea` is GATED — a call creates a PendingConfirmation, not a repo.
3. On confirm (GitHub mocked): repo created with the right name/visibility; README + idea
   files PUT with the idea's content; `promoted_url` set; reply carries the `html_url`.
4. Name-collision (422) and other API failures return an actionable message, no half-state.
5. An already-promoted idea is refused (no duplicate repo).
6. `list_ideas` shows the promoted marker.
7. `create_project_from_idea` is top-level only and refused inside a sub-agent (gate is
   structural).

## 9. Deferred

Org/team target repos; choosing a license/template; a real project scaffold (dirs, CI);
"promote to an existing repo"; auto-linking the idea's original `jarvis-ideas` markdown.
