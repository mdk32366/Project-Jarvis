# TDD — Forking an existing repository

**Status:** draft, for ratification.
**Motivating event:** 2026-08-04, JARVIS was asked to clone a public MANET repo and
answered correctly that she could not — *"no git/repo-cloning tool in my kit; my only
repo tools create new repos."* That is an accurate self-report and the right refusal.
This TDD closes the gap.

---

## 1. What is actually being asked for

"Clone that repo I saw on YouTube so I can work from it." Three different operations
hide behind the word *clone*, and they have materially different properties:

| Operation | GitHub API | Keeps upstream link | Can target private | Needs git + disk |
|---|---|---|---|---|
| **Fork** | `POST /repos/{owner}/{repo}/forks` | Yes | **No** | No |
| **Template instantiate** | `POST /repos/{owner}/{repo}/generate` | No (clean history) | Yes | No |
| **True mirror** | clone + push | No | Yes | **Yes** |

**A true mirror is architecturally unavailable and should be recorded as such rather
than re-proposed each time it comes up.** JARVIS runs on a 512 MB shared-cpu Fly VM
with no persistent volume and no git binary in the image. Shelling out to git would
mean a new dependency, a writable working directory, and unbounded disk against an
unbounded repo size, on the machine that also serves voice webhooks. That is not a
tuning problem; it is the wrong machine for the job.

**Template instantiate only works if upstream declared itself a template.** Most repos
have not. It is worth attempting opportunistically (§4.4) but cannot be the primary
path.

**Fork is the primitive that is actually available.** This TDD builds fork, records
template as an opportunistic upgrade, and records mirror as rejected with its reason.

## 2. The scanner question — and why fork does not violate the invariant

The ratified safety property from TDD #3 §11.3 is **scanner-precedes-public**: every
byte written to a public repo is passed through `app/secretscan.py` before any GitHub
client is constructed. Public-by-default was only acceptable because of it, and the
two may not be separated.

A fork appears to break this outright. It publishes an entire upstream repository —
arbitrarily large, arbitrarily many files — under the owner's account, and JARVIS
cannot scan it: she has no disk to fetch it to, and the Contents API would mean an
unbounded walk over an unknown tree.

**The invariant is not violated, and the reasoning must be written down rather than
assumed, because the next person to read the fork path will reach for an exemption.**

> Forking a repository that is **already public upstream** exposes no byte that was
> not already exposed. The scanner exists to stop *JARVIS's own writes* — scaffold
> renders, idea bodies captured verbatim from SMS and voice, design documents — from
> carrying a credential into public view. A fork writes none of those. It republishes
> bytes whose publication decision was already made by someone else.

**The rule that follows: fork only what is already public upstream. Refuse otherwise.**

That refusal is what *preserves* the invariant rather than exempting the path from it.
If upstream is private (or the visibility read fails, or returns anything other than a
confident "public"), JARVIS refuses and says why. She does not fork it manually-ish,
she does not offer a workaround, she names the constraint.

This also happens to be forced by GitHub: **you cannot fork to private.** A fork of a
public repo is public. So "fork only public upstream" is not a restriction we are
adding on top of the API — it is the API's own shape, and our safety reasoning agrees
with it. That agreement is worth noticing; where a safety argument and a platform
constraint point the same way, the resulting rule is unusually stable.

## 3. Gating

**GATED**, registered via `register_gated` in `backend/app/handlers/repos.py`, exactly
like `create_project_repo`.

The reasoning is identical: creating a named repository is irreversible in the way a PR
is not. The name is taken permanently, it is visible, and undoing it is a manual
deletion. A fork is *more* visible than a fresh repo, because it appears on the
upstream project's fork list under the owner's name.

**The readback states, and this is non-negotiable:**

- the **upstream** `owner/repo` being forked
- the **new** repo name under `_owner()`
- **visibility: public** — stated plainly, not implied
- that the fork will be **publicly attributed to the upstream project**

The last item is new relative to `create_project_repo`'s readback and it is the point
of difference. Forking is a public act on someone else's project. The owner should
know that before it happens, not discover it.

**Top-level registration only.** Gated tools live in the top-level registry; sub-agents
refuse them. Absent from `VOICE_TOOLS_PHASE1`, so it is not voice-reachable — creating
a public repo attributed to a third party from a caller-ID-authenticated channel is not
a thing that should be possible.

## 4. Behaviour

### 4.1 Resolve upstream — never guess

Accept `owner/repo`, or a full `https://github.com/owner/repo` URL, and normalise. A
bare repo name with no owner is **refused with a question**, not resolved by search.
Guessing which `openmanet` was meant is the fabrication failure in a new costume.

### 4.2 Read upstream before writing

`GET /repos/{owner}/{repo}` first. This establishes four facts, and each has a
refusal attached:

| Fact | If wrong |
|---|---|
| The repo exists | Refuse, say so. Do not offer near-matches. |
| It is **public** | Refuse (§2). Name the constraint. |
| It is not already forked by us | Report and **adopt**, do not re-fork |
| Whether it is a **template** | Route to §4.4 |

Record the upstream's **licence** (`license.spdx_id`) in the result string. Not as a
legal opinion — JARVIS is not a lawyer and must not render one — but as a fact the
owner will want at the moment of forking and will not go looking for later.

### 4.3 Fork is asynchronous

`POST /repos/{owner}/{repo}/forks` returns **202 Accepted**. The repository may not
exist yet when the call returns; GitHub's own documentation says it can take up to
five minutes for a large repo.

**Do not poll in the tool.** The orchestrator has a 6-iteration budget and voice has a
~40s poll window. Report the fork as *initiated*, with the expected URL, and say
plainly that it may take a moment to appear.

**This is a truthfulness constraint, not a convenience one.** Reporting "created" for
a 202 is the same defect as `create_project_from_idea` swallowing failed seed PUTs and
claiming success (TDD #3 §11.8) — a partial outcome reported as a whole one. The
existing code already gets this right for partial scaffold seeds; the fork path must
not reintroduce it.

Log a `github_write_log` row with `operation="fork"`. **This extends the invariant that
every GitHub write path writes a row here** — the invariant TDD #3 §7 named as the one
thing that must hold as writers are added. `ok=True` here means *the fork was
accepted*, and the column means what it says: not that the repo is ready.

### 4.4 Template repos — opportunistic, not primary

If §4.2 found `is_template: true`, offer `POST /repos/{owner}/{repo}/generate`
instead, in the readback, as a choice:

> *"That repo is a template — I can generate a clean copy with no fork link and no
> upstream history, and that one can be private. Or I can fork it and keep the link.
> Which?"*

This is the one case where the owner gets a real choice worth surfacing, because the
two outcomes differ in ways he will care about six months later.

**If generate is chosen, the scaffold question returns** — a generated repo is our
write in a way a fork is not. Ratification needed (§7.2).

### 4.5 No scaffold on a fork

**A fork is not seeded with the KEEL scaffold.** The upstream has its own README,
its own structure, possibly its own `docs/`. Writing ours over it would clobber real
work — the same reasoning that makes `create_project_repo` *adopt* an existing repo
rather than re-seed it.

The consequence is that a forked project has no `docs/` tier convention. That is
acceptable: `commit_document` derives its path from the tier and creates the path on
demand, so the convention arrives with the first document rather than at creation.

State this in the tool result so it is not experienced as a surprise.

### 4.6 Provenance is recorded

A forked project must be able to answer *"did I write this or fork it?"* — the
question that actually gets asked six months later, and the one a bare `repo_url`
cannot answer.

Add `Project.upstream_url` (nullable, default empty). Set on fork; empty on
`create_project_repo` and `create_project_from_idea`. Surfaced in `project_status` and
in the Admin Projects panel.

**Migration required.** Draft number `0030`; confirm against `alembic heads` at Step 0
of the build order — the arc has produced five stale migration numbers and the
standing rule is to confirm against the live head, never a draft.

## 5. The routing problem this must also fix

On 2026-08-04 JARVIS bounced between paths and told the owner *"the system doesn't have
OpenMANET as a tracked project."* Three tools now reach nearly the same outcome with
different prerequisites:

| Tool | Prerequisite | Creates |
|---|---|---|
| `create_project_from_idea` | an `Idea` row | repo seeded from the idea |
| `create_project_repo` | a tracked `Project` row | repo seeded with the scaffold |
| `fork_repo` (this TDD) | a tracked `Project` row | fork, no seed |

An owner who says "make me a repo for that thing I mentioned" cannot be expected to
know which precondition he happens to satisfy. The failure mode is not a wrong answer;
it is a refusal that reads as pedantry and costs a round trip at 5am.

**Ratify (§7.3): when the prerequisite is missing but derivable, JARVIS creates it as
part of the confirmed action rather than refusing.** Promoting an idea to a tracked
project is reversible bookkeeping — it is exactly the class the gate is not supposed to
be diluted with. The gate should confirm the *irreversible outcome* (a public repo
exists) once, and the bookkeeping should ride along inside it.

The readback then names both: *"promote Idea #5 to a tracked project and fork
`upstream/openmanet` to `MANETMDK`, public."* One confirmation, one irreversible
outcome, both facts stated.

**This is the honest answer to "how do we delegate more smartly."** It is not fewer
gates, and it is not standing authority. It is one confirmation per irreversible
outcome instead of one per prerequisite.

## 6. What this does not do

- No mirror, no history rewrite, no `git` (§1). Recorded as rejected with reason.
- No syncing a fork with upstream afterwards. Named as the trigger for a follow-on:
  the owner asking for it twice.
- No visibility changes to any existing repo. `create_project_repo` asserts the
  absence of a `PATCH` and this path inherits that; going private stays owner action
  prompted at project close-out.
- **No forking of private upstreams, ever** (§2).

## 7. Open questions for ratification

**7.1 — Is fork gated, or does adopting-an-existing-fork slip the gate?**
Recommendation: fork is gated; *discovering* that we already forked it is a read and
returns without a gate, exactly as `create_project_repo`'s idempotent adopt does.

**7.2 — If a template `generate` is chosen, does the scaffold get applied?**
Recommendation: **no.** A generated repo carries the template author's structure and
overwriting it has the same clobber hazard as a fork. Consistency here is worth more
than scaffold coverage.

**7.3 — Does a missing tracked project get created inside the confirmed action?**
Recommendation: **yes**, per §5. This is the one decision in this TDD with real blast
radius, because it sets a precedent about what may ride along inside a gated
confirmation. The boundary to hold: **only reversible bookkeeping rides along, and
every rider is named in the readback.** Anything irreversible needs its own
confirmation.

**7.4 — Does the fork result state the upstream licence?**
Recommendation: yes, as a fact, with no interpretation.

## 8. Test plan

Offline and pure wherever possible, as with `secretscan` and `render_scaffold`.

| Property | Plant |
|---|---|
| Private upstream is refused | Make the visibility read return `public` unconditionally |
| Refusal precedes any fork POST | Assert the HTTP client is not constructed; plant a check-too-late defect |
| 202 is reported as initiated, never as created | Make the result string say "created" |
| A `github_write_log` row is written with `operation="fork"` | Suppress the log write |
| An already-forked upstream adopts, does not re-fork | Remove the existence check |
| Scaffold is NOT written on a fork | Add a scaffold seed call |
| `upstream_url` is set on fork and empty on the other two paths | Set it on `create_project_repo` |
| Bare repo name with no owner is refused | Add an owner default |

**§2.7 on every plant:** inject a value no branch can legitimately produce. Do not
plant visibility with `"public"` — it coincides with the expected output and cannot
redden the branch that legitimately produces it.
