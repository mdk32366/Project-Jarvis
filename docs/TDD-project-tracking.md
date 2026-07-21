# TDD — Project & Milestone Tracking

**Status:** Draft, ready to build
**Date:** 2026-07-21
**Series:** 1 of 3 (project tracking → planning sessions → repo scaffolding)
**Depends on:** existing `ideas` table (migration 0004), `tasks` table (0004),
runtime settings overlay (PR #28)

---

## 1. Problem

There is no durable answer to "where am I on this?"

Every multi-session arc — the health loop, the Tasker inversion, KEEL, Duffel
activation — has lived in session close-out documents and Matt's head. Close-outs
are excellent narrative records and terrible state stores: you cannot query them,
they go stale the moment work happens, and the answer to "what's left on X"
requires reading a 200-line document written for a different purpose.

The `ideas` table already commemorates things worth doing. Nothing tracks the
things being *done*.

## 2. Goals

1. A durable, queryable record of multi-session work: what it is, what state it's
   in, what's left.
2. **Milestones** that can be marked done, so progress is recorded at the moment
   it happens rather than reconstructed later.
3. A project knows **its repo and its documents**, so "where am I" answers with
   the live TDD, not just a title.
4. **Promotion from idea → project**, preserving the idea where it lives.
5. Answerable by voice and SMS, not just the Admin page. The question gets asked
   from a boat.

## 3. Non-goals

- **Replacing `tasks`.** Tasks are discrete actions with due dates. Projects are
  multi-session arcs. Two stores is only a problem if the boundary is unclear —
  §4.1 makes it explicit.
- Replacing session close-out documents. Those carry reasoning and lessons; this
  carries state. Complementary, and the project points at the documents.
- Gantt charts, dependencies between milestones, effort estimation, burndown.
  Milestones are ordered and completable. That is the whole model.
- Syncing to Google Tasks or any external tracker.

---

## 4. Design

### 4.1 The boundary against `tasks`

Stated once, plainly, because ambiguity here is how you end up trusting neither
store:

> **A task is a discrete action with a due date.** "Call the marina." "Set
> `AUTOREMOTE_KEY`." It is done in one sitting and then it is gone.
>
> **A project is a multi-session arc with milestones.** It survives close-outs,
> accumulates documents, and has a state beyond done/not-done.

A milestone is *not* a task. A milestone is a checkpoint within a project
("health checks split", "phone reconfigured"). If something wants a due date and
a reminder, it is a task. If it wants to be a line in "where am I," it is a
milestone.

Nothing enforces this at the schema level and nothing should — it is a judgment
call and the cost of getting it wrong once is trivial.

### 4.2 Project state

`active` / `parked` / `done` / `abandoned`.

**`parked` carries a required reason.** This is the design decision worth calling
out. Matt's world already has a parked tier — R17 multi-repo provenance, R19 push
alerting, Duffel live-mode — and parked-with-a-reason is a completely different
thing from stalled. R19 is parked *until the false-positive rate is known*: that
is a resumption condition, and storing it means the project can tell you when to
look at it again instead of quietly rotting.

`abandoned` is distinct from `done` and both are terminal. A project that was
tried and rejected is a real outcome worth keeping — the alternatives-rejected
discipline applies to projects, not just designs.

### 4.3 Documents

A project points at its documents, each with a **tier** mirroring the established
`docs/` convention:

- `live` — the current design record
- `archive` — superseded, kept for history
- `operational` — executed handoffs and checklists

This is deliberately the same three-way split as the repo, because the repo
convention already encodes the right idea: **files are sorted by whether they are
live, superseded, or spent — not by topic.** That is what prevents two sources of
truth for the same design. A tracker that lists documents without that
distinction would reintroduce exactly the ambiguity the convention was invented
to kill.

Consequence worth stating: asking "what's the design for X" returns the `live`
TDD, singular. If two documents of the same kind are `live` on one project, that
is a defect, and §7 surfaces it.

### 4.4 Promotion from idea

An idea is promoted, not consumed:

- `ideas.status` moves `idea` → `promoted`
- a `project` row is created with `idea_id` pointing back

The idea stays where it is, still commemorated, still in the `jarvis-ideas`
commit sink. Promotion is a status change plus a link — never a move or a delete.
The origin of a project is part of its history.

---

## 5. Data model

### 5.1 New table — `project`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | text, not null, unique | short handle: "Location Pull Inversion" |
| `summary` | text | one-paragraph what-and-why |
| `status` | enum | `active` / `parked` / `done` / `abandoned` |
| `parked_reason` | text, null | **required when `status='parked'`** |
| `repo_url` | text, null | set by TDD #3 scaffolding; null until then |
| `idea_id` | int FK → `ideas.id`, null | origin, if promoted |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | touched by any milestone or status change |
| `completed_at` | timestamptz, null | set on `done` **or** `abandoned` |

Index on `status`, on `idea_id`.

### 5.2 New table — `milestone`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `project_id` | int FK → `project.id`, not null, ON DELETE CASCADE | |
| `title` | text, not null | |
| `detail` | text | optional |
| `position` | int, not null | display order; sparse (10, 20, 30) so insertion doesn't renumber |
| `status` | enum | `open` / `done` / `dropped` |
| `completed_at` | timestamptz, null | |
| `created_at` | timestamptz | |

`dropped` exists for the same reason `abandoned` does: a milestone that stopped
being relevant is not a milestone that was achieved, and collapsing them
overstates progress.

Index on `(project_id, position)`.

### 5.3 New table — `project_document`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `project_id` | int FK → `project.id`, not null | |
| `kind` | text | `tdd` / `test-plan` / `ui-plan` / `closeout` / `readme` / `other` |
| `tier` | enum | `live` / `archive` / `operational` |
| `title` | text, not null | |
| `path` | text | repo-relative, e.g. `docs/TDD-location-pull-inversion.md` |
| `url` | text, null | resolved GitHub URL if known |
| `superseded_by_id` | int FK → self, null | set when a doc moves to `archive` |
| `created_at` | timestamptz | |

`kind` is free text with conventional values rather than a hard enum —
document kinds will grow, and a migration per new kind is friction with no
benefit.

Index on `(project_id, tier)`.

### 5.4 Modified table — `ideas`

Add `status` (`idea` / `promoted` / `dropped`), default `idea`, if not already
present. **Verify against the live schema first** — the table dates to migration
0004 and may have drifted.

### 5.5 Migration

`0022_projects.py` — three new tables, `ideas.status` add-if-absent, indexes as
above. No backfill; existing arcs get entered by hand or not at all.

---

## 6. Tools

All read tools available on every channel. Writes follow existing conventions —
none of these are irreversible, so none are gated.

| Tool | Notes |
|---|---|
| `create_project(name, summary, status='active')` | |
| `promote_idea(idea_id \| title, ...)` | idea → `promoted`, creates linked project |
| `list_projects(status='active')` | defaults to active — the common question |
| `project_status(name)` | the "where am I" answer; see §6.1 |
| `add_milestone(project, title, detail=None, after=None)` | `after` for ordering |
| `complete_milestone(project, milestone)` | fuzzy-matches title within project |
| `drop_milestone(project, milestone, reason)` | |
| `set_project_status(project, status, reason=None)` | **rejects `parked` without a reason** |
| `attach_document(project, kind, tier, title, path)` | called by TDD #2 and #3 |
| `supersede_document(doc, by_doc)` | old → `archive`, sets `superseded_by_id` |

`complete_milestone` is the one that gets used most and from the worst input
device. It should accept a partial title and, on ambiguity, ask rather than
guess — completing the wrong milestone is a silent data error that looks like
progress.

### 6.1 `project_status` — exception-first

Returns, in this order:

1. Name, status (with parked reason if parked), milestone counts done/total
2. **Next open milestone** — the single most useful field
3. The `live` TDD, if one exists
4. Anything wrong: no live TDD, two live TDDs of the same kind, no open
   milestones on an `active` project, no update in 30+ days

Point 4 is the standing exception-reporting discipline: **surface what's wrong,
not what's fine.** An `active` project with no open milestones and no recent
update is either finished or stalled, and either way the record is lying.

---

## 7. Health check — `project_hygiene`

One new component, informational.

- `ok` — no anomalies
- `degraded` — one or more of: `active` project with zero open milestones;
  two `live` documents of the same `kind` on one project; `active` project
  untouched in 30 days
- `unknown` — no projects exist

Never `down`. A stale project record is a bookkeeping problem, not a system
fault, and inflating it to `down` would train the eye to ignore the status page —
which is the failure mode the exception-first design exists to prevent.

Seeded and reconciled on startup, **tools and descriptions both**.

---

## 8. Admin UI

One panel on the existing Admin page:

- Active projects, each with milestone progress and next open milestone
- Expand → full milestone list with a completion control, documents by tier
- Parked section, collapsed, showing each reason
- Done/abandoned behind a toggle

Milestone completion from the UI hits the same tool path as voice, not a separate
endpoint. One write path, one set of tests.

---

## 9. Build order

| # | Work | Testable |
|---|---|---|
| 1 | Migration 0022, models | ✅ |
| 2 | Tools + `project_status` composition | ✅ |
| 3 | Promotion path (`ideas.status`, link) | ✅ |
| 4 | `project_hygiene` check + seed | ✅ |
| 5 | Admin panel | ✅ |
| 6 | Backfill current arcs by hand | manual |

Merge-on-green authorized for 1–5 per the standing decision.

---

## 10. Test plan

- **Promotion preserves the idea** — idea row still exists, `status='promoted'`,
  project links back via `idea_id`. Assert the idea was **not** deleted.
- **Parked requires a reason** — `set_project_status(p, 'parked')` with no reason
  raises; with a reason succeeds and the reason is retrievable.
- **Milestone ordering** — `after=` inserts between two existing milestones
  without renumbering the rest.
- **Completion is idempotent** — completing an already-`done` milestone does not
  move `completed_at`.
- **Ambiguous milestone match asks** — two milestones matching a partial title
  produce a disambiguation request, **not** a completion. Assert nothing was
  written.
- **Dropped ≠ done** — a dropped milestone does not count toward progress.
- **Cascade** — deleting a project removes its milestones and documents.
- **Two live TDDs is degraded** — attach two `live` `tdd` docs to one project;
  assert `project_hygiene` reads `degraded` and `project_status` surfaces it.
- **Empty active project is degraded** — active, zero open milestones.
- **No projects → `unknown`, not `ok`** — the no-evidence rule.

---

## 11. Open questions

- **Voice ergonomics of `complete_milestone`.** Fuzzy title matching over a phone
  call, with disambiguation, may be clumsy in practice. Worth using for a week
  before adding anything cleverer; the failure is visible and harmless.
- **Milestone templates.** Every build so far has roughly the same shape (design
  → migration → tools → checks → UI → verify). A template could pre-populate.
  Deferred: premature until several real projects exist to generalize from.
- **Do close-out documents auto-attach?** They are produced in sessions like this
  one, outside JARVIS. Manual `attach_document` for now; TDD #2 may make it
  automatic for documents it authors.
