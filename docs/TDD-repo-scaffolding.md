# TDD — Repo Scaffolding & Document Commits

**Status:** Draft, **reconciled against the code 2026-08-01** — see §11. Several
§4–§8 claims were written without reading the shipped GitHub paths and are false
or unachievable as stated. Read §11 before building; the affected sections carry
inline markers.
**Date:** 2026-07-21 (draft) / 2026-08-01 (reconciliation pass)
**Series:** 3 of 3 (project tracking → planning sessions → **repo scaffolding**)
**Depends on:** TDD #1 (`project`, `project_document`), TDD #2 (emission),
existing `GITHUB_TOKEN` and the ideas commit path

---

## 1. Problem

JARVIS can produce a design document (TDD #2) and track a project (TDD #1), but
has nowhere to put the document. It needs to land in version control, in the
right repo, in the right tier of the `docs/` convention.

Two destinations, established by Matt on 2026-07-21:

- **A new capability for JARVIS** → `Project-Jarvis/docs/`. KEEL applies exactly
  as it does today.
- **A new project** → a **new repo**, scaffolded with the standard structure,
  document in that repo's `docs/`.

The second requires **repo creation**, which is a materially larger permission
than the file writes JARVIS does today.

## 2. Goals

1. Commit a document to `Project-Jarvis/docs/` on a branch, as a PR.
2. Create and scaffold a new project repo with the standard structure.
3. Enforce the `docs/` tier convention — `live` / `archive` / `operational` — at
   write time, not by hope.
4. Record what was written in `project_document` (TDD #1) so the tracker and the
   repo cannot silently diverge.

## 3. Non-goals

- Committing code. Documents only. JARVIS writes designs, not implementations —
  the Planner/Builder split again.
- Merging PRs. A PR is opened and left for review. **JARVIS never merges.**
- Deleting repos, files, or branches. There is no destructive path here at all.
- Managing collaborators, settings, or branch protection beyond initial creation.

---

## 4. Design

### 4.1 Two operations, two risk profiles

**Document commit** — reversible. A branch and a PR; nothing touches `main`
without human review. Ungated, consistent with the existing ideas commit path
which already writes to GitHub without a gate.

**Repo creation** — **gated.** Creating a repo under Matt's account is not
reversible in the way a PR is: it takes a name permanently, it is visible, and
undoing it is a manual deletion. This is squarely the irreversible-action class,
and it gets the standard treatment.

The gate is on creation only. Once a repo exists and is recorded on the project,
document commits into it are ungated like any other.

### 4.2 Token scope — a real prerequisite

> **RECONCILED 2026-08-01 — the premise below is FALSE.** `create_project_from_idea`
> (`app/handlers/ideas.py:195`) already calls `POST /user/repos` with the single
> `GITHUB_TOKEN` and creates repos successfully in production. A classic PAT with
> `repo` scope *does* permit creating repositories under the owner's own account.
> `GITHUB_ADMIN_TOKEN` is therefore **not a prerequisite** and no owner action is
> blocked on minting one. The blast-radius argument for splitting tokens survives
> on its own merits and may still be worth doing — but it must be re-argued as a
> defence-in-depth choice, not inherited as a capability requirement. See §11.2.

The existing `GITHUB_TOKEN` has `repo` scope, set for the ideas commit sink.
**`repo` does not permit repository creation.** A classic PAT needs `public_repo`
for public repos, or full `repo` plus account-level create permission; a
fine-grained PAT needs explicit "Administration: read and write."

Therefore:

- **`GITHUB_TOKEN`** — unchanged, retains `repo`. Used for all document commits.
- **`GITHUB_ADMIN_TOKEN`** — new, narrower audience, create permission only. Used
  *solely* by the repo-creation path.

Two tokens rather than upgrading one, deliberately. The high-privilege token is
reachable from exactly one gated code path, so the blast radius of the common
path stays where it is today. Upgrading the existing token would silently grant
creation rights to every existing GitHub call site.

Owner action: generate on desktop, set as a Fly secret, record in the password
manager at creation. Fly secrets are write-only once set.

### 4.3 Repo visibility — KEEL doctrine

> **RECONCILED 2026-08-01 → RATIFIED: public stands.** Shipped code defaults the
> opposite way (`_create_project_from_idea`: `private = bool(args.get("private", True))`,
> pinned by `tests/test_ideas.py:294`). The owner ratified **this section**, not the
> code: KEEL doctrine wins, new project repos are created public by default, and the
> code changes to match. See §11.3 for the consequence — **this makes the secret
> scanner a precondition of the flip, not a companion to it.**

New project repos are created **public** by default, per current KEEL doctrine:
the Planner AI (browser chat) can only connect directly to public repos, so a
private repo on day one cannot be brought into a session like this one.

The go-private trigger is unchanged and is **owner action, not automated**: first
real credential, first real user data, or first live deploy — whichever comes
first. JARVIS does not flip visibility.

**Consequence that must be enforced, not assumed:** a public repo means every
document committed is public at the moment of the commit. §4.5.

### 4.4 The scaffold

Mirrors the structure proven in `Project-Jarvis`:

```
README.md
ARCHITECTURE.md
docs/
  README.md          ← carries the convention itself
  archive/.gitkeep
  operational/.gitkeep
.gitignore
```

`docs/README.md` is the important file. It carries the convention verbatim,
including the organizing principle:

> Files are sorted by whether they are **live**, **superseded**, or **spent** —
> not by topic. `docs/` holds live design records and active references.
> `docs/archive/` holds superseded documents, each with a banner naming what
> replaced it. `docs/operational/` holds executed handoffs and checklists.
>
> Commit the design before the work is done.

The scaffold is a **stored, versioned template** in `Project-Jarvis`, not
reconstructed from the model's memory per invocation. A structure regenerated
from memory each time will drift, and drift in the thing whose entire job is
preventing drift is a special kind of failure.

### 4.5 Secret scanning — mandatory, pre-commit

> **RECONCILED 2026-08-01 — the ordering claim is already unachievable, and the
> scope is wider than "every document".** There is no scanner anywhere in
> `backend/app/` today, and **three GitHub write paths already ship**: the
> `commit_idea` job, and the two repo-seed PUTs inside `create_project_from_idea`.
> The scanner is a **retrofit**, not a precondition, and it must cover those
> existing writers — not only `commit_document`. This matters more than it sounds:
> idea bodies are free text captured from SMS and voice and written to GitHub
> **verbatim**, which is exactly where a pasted credential would land. See §11.4.
> This is the highest-value item in the TDD and it is independent of everything
> else here — it should ship first and on its own.

Every document is scanned **before** the commit call, not after:

- high-entropy strings above a length threshold
- known token prefixes: `ghp_`, `github_pat_`, `duffel_`, `sk-ant-`, `AC` +
  32 hex (Twilio SID), `xoxb-`, `AIza`
- anything matching the values of known Fly secret names, if resolvable
- private key headers

A hit **blocks the commit** and reports the match location without echoing the
matched value.

This is not optional caution. KEEL's own doctrine is that building public makes
secrets discipline *structurally enforced rather than optional*, and the
automatic scanner belongs in the gate. A machine that writes to public repos
without one is the exact hazard that doctrine names.

---

## 5. Data model

No new tables. Additions only:

- `project.repo_url` (already in TDD #1 §5.1) — set on creation
- `project_document.url` — set on successful commit
- `github_write_log`: `id`, `operation` (`create_repo` / `commit_doc` /
  `open_pr`), `target`, `ref`, `ok`, `error`, `created_at`

The write log exists so a failed or partial write is diagnosable after the fact.
Everything else here reuses TDD #1's schema.

Migration `0024_github_writes.py`.

> **RECONCILED 2026-08-01.** Both "additions" already exist and need no
> migration: `Project.repo_url` (`String(400)`) and `ProjectDocument.url`
> (`String(400)`) shipped with TDD #1 in migration 0024. **`github_write_log` is
> the only real schema work here.**
>
> The migration number is wrong and so is inception's. `0024` is
> `0024_projects`; live head is `0025_capability_rollup`. In the ratified build
> order (#2 → #3 → inception) the slots are **0026 planning sessions, 0027 github
> writes, 0028 inception** — note that inception's reconstruction "corrected"
> itself to 0026, which only holds if it lands *first*, contradicting its own
> dependency order. All three drafts cite stale numbers; confirm against the live
> head at build time, every time. See §11.5.

---

## 6. Tools

| Tool | Gated | Notes |
|---|---|---|
| `commit_document(project, kind, tier, title, body)` | no | branch + PR |
| `create_project_repo(project, name, visibility='public')` | **yes** | §4.1 |
| `list_project_repo(project)` | no | read |

### 6.1 `commit_document`

1. Scan for secrets (§4.5). Abort on hit.
2. Resolve destination repo: `Project-Jarvis` when the project is JARVIS herself,
   otherwise `project.repo_url`. **Abort if unresolvable** — never guess a repo.
3. Resolve path from tier: `docs/`, `docs/archive/`, `docs/operational/`.
4. Branch `docs/<slug>-<yyyymmdd>`.
5. Commit, open PR, **never merge**.
6. `attach_document(...)`, record in `github_write_log`.

Path resolution from tier is the enforcement point for the convention. A caller
cannot write an archive document into `docs/` because it does not supply the
path — it supplies the tier.

### 6.2 `create_project_repo`

> **RECONCILED 2026-08-01 — this is a REFACTOR, not a new build.** Everything
> below exists in `_create_project_from_idea`: gated registration via
> `ideas.register_gated`, a `pregate` that refuses unknown/already-promoted/
> unconfigured/unnamed, a `summarize` that reads back name and visibility, repo
> creation, scaffold seeding, and idempotence on 422-already-exists. The work is
> to **generalise it from `idea` to `project`** and keep the idea path working
> over the same seam — not to write it again. Writing it again produces two
> gated repo-creation paths, and the second one is the one nobody remembers to
> audit. See §11.6.

Gated: confirmation with readback of repo name, visibility, and owner before
anything is created. Uses `GITHUB_ADMIN_TOKEN` exclusively.

1. Create repo (`public` default, §4.3)
2. Commit the scaffold as an initial commit
3. Set `project.repo_url`
4. Log

**Idempotence:** if the repo already exists, do not fail destructively and do not
attempt to overwrite. Report it, set `repo_url` if unset, stop. A half-created
repo from a network failure must be recoverable by re-running.

---

## 7. Health check — `github_writes`

> **RECONCILED 2026-08-01 — there is no `github` component at all today.** The
> GitHub half of Ideas is entirely invisible to health: `capture_idea`,
> `list_ideas` and `get_idea` all map to `postgres` in `_TOOL_COMPONENT`, which is
> honest for the DB write and says nothing about whether the commit landed. Adding
> this check means adding a **component**, and the PR #50 guards then require a
> runbook for every fault code it can emit and none for any code it cannot.
>
> **Read the write log, not `actions_audit` — and that is load-bearing, not
> incidental.** The routine exercise path for GitHub is the `commit_idea` **job**
> (`app/jobs.py:264`), not a tool call, so an audit-derived liveness check would be
> starved on day one — the calendar latch, rebuilt from scratch, in the same week
> it was swept. Reading `github_write_log` sidesteps it, on one condition that must
> be stated as an invariant: **every GitHub write path writes a row, the job
> included.** See §11.7.

- `ok` — no failed writes in the trailing 7 days
- `degraded` — any `ok=false` in `github_write_log` in 7 days
- `unknown` — no writes ever
- never `down` — inability to commit a document is not a system fault

Remediation runbook: token validity and scope, rate limit, repo existence,
branch conflict.

---

## 8. Build order

| # | Work | Testable |
|---|---|---|
| 1 | Migration 0024, `github_write_log` | ✅ |
| 2 | **Secret scanner** — standalone, first | ✅ |
| 3 | `commit_document` → `Project-Jarvis/docs/` | ✅ (mock API) |
| 4 | Scaffold template stored in repo | ✅ |
| 5 | `create_project_repo`, gated | ✅ (mock) then live |
| 6 | Health check + wire TDD #2 emission | ✅ |

Scanner before any write path exists. A writer built first is a writer that works
without the scanner, and then the scanner is an addition rather than a
precondition.

---

## 9. Test plan

- **Secret scanner catches each prefix** — `ghp_`, `duffel_`, `sk-ant-`, Twilio
  SID, private key header. One test per pattern.
- **Scanner blocks the commit** — a document containing a token produces no API
  call at all. Assert the client was never invoked, not merely that it returned
  an error.
- **Scanner does not echo the secret** — assert the matched value is absent from
  the error message and from `github_write_log`.
- **Tier → path** — `live`→`docs/`, `archive`→`docs/archive/`,
  `operational`→`docs/operational/`. A caller-supplied path is ignored.
- **Never targets `main`** — assert every commit is on a `docs/` branch.
- **Never merges** — assert no merge call exists on the path. Asserted in test.
- **Unresolvable repo aborts** — project with null `repo_url` and target
  `new_project` → abort, no API call, no guess.
- **Repo creation is gated** — ungated invocation refuses; assert nothing created.
- **Creation idempotence** — existing repo → reports, sets `repo_url`, does not
  overwrite the scaffold.
- **Admin token isolation** — assert `commit_document` never reads
  `GITHUB_ADMIN_TOKEN`. This is the §4.2 argument; assert it rather than trusting
  it.
- **Scaffold completeness** — created repo contains all files from §4.4, and
  `docs/README.md` contains the convention text.

---

## 10. Open questions

- **Repo naming.** Derive from project name, or ask? Deriving is smoother;
  asking is safer given the name is permanent. Lean: propose a derived name in
  the gate readback, let Matt override. The gate makes this nearly free.
- **Does a new project repo need a CI gate from day one?** KEEL says the
  foundation comes before features, which argues for scaffolding the gate
  workflow too. Deferred from v1 — a docs-only repo has nothing to test, and a
  gate that tests nothing is theatre. Revisit when a scaffolded repo first gets
  code.
- **Scanner false-positive rate.** Aggressive entropy detection will flag base64
  in a design document. Start with prefix matching plus a conservative entropy
  threshold; tune from real refusals rather than from imagination.
- **Should `Project-Jarvis` document commits go through the same gate as code?**
  Currently no — they are docs, on a branch, reviewed as a PR. If a document
  commit ever lands in a path that CI reads, revisit.

---

## 11. Reconciliation against the code — 2026-08-01

The pre-work note (`PREWORK-project-management-arc.md`) instructed: *"Read the
existing code before speccing the build… do not design around a capability that
is partly built."* This is that pass. It was warranted — **five of this TDD's
claims are false or unachievable as written, and one of them would have blocked
the build on owner action that isn't needed.**

The general finding is worth stating before the specifics: this TDD was drafted
as greenfield, and **it is not greenfield.** A working, gated, production-proven
GitHub repo-creation path already exists under the Ideas agent. Building §6.2 as
specified would produce a *second* gated repo-creation path beside the first.

### 11.1 What actually exists today

| Capability | State | Where |
|---|---|---|
| Repo creation (`POST /user/repos`), **gated**, with pregate + readback + 422-idempotence | **SHIPPED** | `handlers/ideas.py:173` |
| Scaffold seeding (`README.md`, `docs/idea.md`) via Contents API | **SHIPPED**, but see §11.8 | `handlers/ideas.py:207` |
| Document commit to a fixed repo (`commit_idea` job), create-or-update with blob sha | **SHIPPED** | `handlers/ideas.py:230` |
| `Project.repo_url`, `ProjectDocument.url`, `attach_document`, tier enforcement | **SHIPPED** (TDD #1) | `models.py`, `handlers/projects.py:408` |
| Secret scanner | **DOES NOT EXIST** | — |
| Branch + PR (Git Refs / Pulls API) | **DOES NOT EXIST** | — |
| `github_write_log` | **DOES NOT EXIST** | — |
| Any `github` health component | **DOES NOT EXIST** | — |

Every existing write is a Contents API `PUT` straight at the default branch.
Nothing in the codebase has ever created a branch or opened a PR.

### 11.2 The two-token prerequisite is not a prerequisite (§4.2)

`repo` scope creates repos fine, and has been doing so in production. The
argument that survives is narrower and worth keeping honestly: a single token
means the *common* path (idea commits) holds creation rights it never uses.
That is a real defence-in-depth point. It is **not** a capability blocker, and
it should not be presented to the owner as owner-action-required.

### 11.3 Visibility default — **RATIFIED 2026-08-01: public**

Code said private by default, pinned by a test. This TDD said public, on the KEEL
argument that a Planner AI in a browser chat can only connect to public repos — so
a private day-one repo cannot be brought into a session like the ones that produced
this arc. **The doctrine wins and the code changes to match.**

Recorded here rather than left as a shared assumption, for the same reason the 24h
session expiry was recorded in `design-note-latch-failures.md` §7: no code changed
at the moment of the decision, so the ratification *is* the deliverable. A
security-relevant default that flips because someone edited a fixture is how
defaults rot.

**The consequence, and it is a hard sequencing constraint, not a note.** §4.3
already says it — *"a public repo means every document committed is public at the
moment of the commit"* — but it is now load-bearing rather than cautionary. Under a
private default, shipping the scaffold before the scanner leaked nothing. Under a
public default it can. Therefore:

> **The secret scanner (build step 1) is a PRECONDITION of the visibility flip
> (build step 6), not a companion to it.** They may not land in the same PR with
> the flip first, and the flip may not be pulled forward "since it's a one-line
> default." KEEL's own argument for building public is that it makes secrets
> discipline *structurally enforced rather than optional* — that argument is only
> honest once the enforcement exists.

**Open sub-decision, deliberately not settled here.** The ratified default was
argued for *project inception* repos, which need to be Planner-connectable. It is
not obvious the same reasoning reaches `create_project_from_idea`, where flipping
the default silently changes a shipped, owner-facing behaviour: "promote idea #7"
currently yields a private repo and would begin yielding a public one, with the
idea body — free text captured from SMS and voice — public at the moment of the
seed commit. Two defensible resolutions: apply the public default uniformly (one
rule, no surprises about which path you are on), or scope it to
`create_project_repo` and leave idea promotion private (the two paths already
serve different purposes, per §11.6). **Decide at build step 6, with the scanner
already in place either way.**

### 11.4 The scanner is a retrofit with live exposure (§4.5)

"Scanner before any write path exists" cannot be satisfied — the write paths
shipped first. Worse than a missed ordering: **idea bodies are captured from SMS
and voice as free text and committed to GitHub verbatim.** That is a live path
from "owner pastes a token into a note" to "token in a git history," today, with
no scanner between them.

The build-order consequence is that the scanner **stops being step 2 of TDD #3
and becomes its own first deliverable**, covering `commit_idea_to_repo` and the
two seed PUTs. It needs nothing else in this TDD to be useful, and it retires a
real exposure rather than a hypothetical one.

### 11.5 Every migration number in the arc is stale (§5)

`0024` is `0024_projects`; head is `0025_capability_rollup`. Planning sessions
cites `0023` (taken by `0023_relay_accepted`). Inception cites `0026`, which is
only free if it lands before #2 and #3 — contradicting its own dependency order.
Correct sequence for the ratified build order: **0026 / 0027 / 0028.** The
standing rule already recorded in the pre-work holds and is now demonstrated
three times over: **numbers are indicative, never reserved.**

### 11.6 §6.2 is a generalisation, not a build

`_create_project_from_idea` → `create_project_repo(project, name, visibility)`,
with the idea path preserved over the same seam. Note that TDD #1's
`_promote_idea` and Ideas' `_create_project_from_idea` are already deliberately
orthogonal — tracking-row vs. GitHub-repo, documented in `Idea.status`. The
refactor must not collapse them; it should let one moment do both, which is
exactly what inception §11 anticipates ("create repo → seed rows → commit plan →
ratify").

### 11.7 The health check must read the write log, or it is born starved (§7)

Stated in §7 above. Recording the reasoning here because it is the design-note
checklist (`design-note-latch-failures.md` §5, Q3 — *"is the routine exercise
path the same path the check reads?"*) applied at design time and returning a
non-obvious answer. The routine GitHub exercise path is a **job**, and jobs write
no `actions_audit` rows. An audit-derived check here would be `unknown` forever
or latched on its first failure. The write log is the correct substrate precisely
because the job can write to it.

### 11.8 A live defect found during the pass — partial repo seeding reports success

Not a doc conflict; a bug in shipped code, found by reading it.

`_create_project_from_idea` seeds `README.md` and `docs/idea.md` in a loop
(`ideas.py:207-217`). A failed `PUT` is **`log.warning`'d and swallowed** — the
loop continues, `idea.promoted_url` is set, and the tool returns
`"Created the project repo: <url>"`. A repo created with a missing or empty
scaffold reports unqualified success, and `promoted_url` being non-empty means a
retry is *refused* as already-promoted.

This is the proxy-signal family, not the latch family (`design-note-latch-failures.md`
§3): it reports the *creation* status while claiming the *seeding* outcome, the
same shape as `relay_accepted` reading the HTTP status instead of the response
body. It has been wrong since the path shipped, not transiently.

Fix belongs with the §6.2 refactor: collect the seed outcomes, and either report
partial success honestly or make the operation re-runnable by not marking
promoted until the scaffold is complete. **`ideas.py:132` `_explain_repo_error`
has the milder version of the same shape** — 401 raises `ToolFault` (audited as a
fault) while 403 and 422 `return` a string (audited `ok`), so a rate-limited or
rejected creation currently looks like a successful tool call to the audit
substrate.

### 11.9 Revised build order

Supersedes §8. The reordering is not cosmetic: it puts the one item with live
exposure first, and refuses to spend effort on a second repo-creation path.

| # | Work | Change from §8 |
|---|---|---|
| 1 | **Secret scanner, standalone** — retrofit onto `commit_idea_to_repo` + the two seed PUTs | Promoted; scope widened to existing writers |
| 2 | Fix §11.8 — partial-seed reporting + `_explain_repo_error` fault classing | **New** |
| 3 | Migration 0027, `github_write_log`; backfill the existing write paths into it | Was step 1; `repo_url`/`url` dropped (exist) |
| 4 | `commit_document` — branch + PR (Git Refs + Pulls API), tier→path enforcement | Genuinely new; was step 3 |
| 5 | Scaffold template stored + versioned in-repo | Unchanged |
| 6 | **Generalise** `create_project_from_idea` → `create_project_repo`; flip the visibility default to public (§11.3) and update `test_ideas.py:294` deliberately | Was "build, gated"; now a refactor + the ratified flip |
| 7 | `github` component + `github_writes` check reading the write log + runbooks | Unchanged, with §11.7's substrate pinned |

Steps 1 and 2 are independent of the project-management arc and can ship without
TDD #2.

**Step 1 gates step 6** (§11.3): the visibility flip may not land before the
scanner covers every writer. This is the one ordering constraint in the list that
is a safety property rather than a convenience — the others can be resequenced if
something more urgent surfaces; this one cannot.
