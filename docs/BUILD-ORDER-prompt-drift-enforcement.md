# BUILD ORDER — Prompt-drift enforcement (the review ledger)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Follows:** the 2026-08-02 audit, `docs/design-note-prompt-drift.md`.
**Merge policy:** merge-on-green. No gate changes, no secrets, no outward-facing
switches. One owner action falls out of it (§5) — flag it, don't do it.

---

## §0 — The decision, and why this shape

The 08-02 audit fixed nine agents and left **two guards**: the secretary's curated
judgment list and the travel kill-switch tie. The other seven have the §5 checklist
— which is process — and nothing that fails.

The defect is **silent growth**: `seed_agents` reconciles rosters and never touches
`system_prompt`, so every new tool ships and none of the prose does. Nothing goes
red. It is the unwatched-instrument shape exactly: a known defect class with a
documented remedy and no alarm.

**Two candidate shapes were considered:**

| Shape | Catches | Costs |
|---|---|---|
| Per-agent curated guidance lists (the secretary pattern, ×7) | The specific gaps we already know about | Seven hand-maintained lists that drift |
| **Structural: fail when a roster grows past a reviewed baseline** | Drift we haven't thought of yet, including future tools | One ledger, touched by the same PR that adds the tool |

**Ratified: the structural one.** The defect is silent growth, not any particular
missing sentence. A guard that only knows today's gaps cannot catch tomorrow's, and
tomorrow's is the whole problem.

But the two shapes are not exclusive, and §2 gets both for the price of one — the
curated list becomes a *byproduct* of review rather than a separate artifact to
maintain.

---

## Step 0 — Confirm state

`alembic heads` (expected `0029_plan_draft_status`, unchanged — **no migration in
this order**), clean tree. If you find a reason for a migration, stop and report:
the ledger is deliberately a code file, not a table (§1.2).

---

## Step 1 — The review ledger

New module, `backend/app/prompt_review.py`.

### 1.1 Shape

A mapping of agent → tool → disposition, where disposition is one of exactly two
values:

- `"guided"` — this tool's schema leaves a real decision unmade (when to reach for
  it unprompted, which of two similar options, what not to promise). **The prompt
  must mention it.**
- `"self-describing"` — the schema carries everything the agent needs. The roster
  alone is sufficient.

Seed it from the current state: every tool currently on every agent's
`DEFAULT_AGENTS` roster, with the secretary's existing `_JUDGMENT_TOOLS` entries
marked `guided` and the rest assessed against the §5 checklist. **Assess them;
don't bulk-mark everything `self-describing` to get to green.** A ledger that
starts as a rubber stamp teaches the next reader that it is one.

Record a one-line reason next to each `guided` entry. It costs nothing and turns
the file into a record of judgment rather than a list of strings.

### 1.2 Why a code file and not a table

- It is version-controlled, so the disposition is reviewable in the PR that adds
  the tool.
- The PR adding a tool to a roster naturally touches the file next to it.
- A table would need a migration, an admin surface, and a seeding path — and would
  put the record of a *review decision* somewhere a deploy could reconcile it
  away, which is the original defect wearing a new hat.

State that reasoning in the module docstring.

---

## Step 2 — Three guards

`backend/tests/test_prompt_review.py`.

### 2.1 Coverage — fails on growth

Every tool on every agent's `DEFAULT_AGENTS` roster appears in the ledger. Failure
message names the agent and the specific tools:

```
secretary: 2 tools added since last prompt review: ['location_ping_log', 'emit_project_plan']
Review each against design-note-prompt-drift.md §5, update the prompt if it needs
a line, then record the disposition in prompt_review.py.
```

**This is the guard that does the work.** Adding a tool to a roster now fails CI
until someone has consciously decided whether it needs prose. The act of adding
the ledger entry *is* the acknowledgement.

### 2.2 Guidance — the secretary guard, generalised

Every tool marked `guided` appears in that agent's seed `system` text. This is
`test_the_secretary_prompt_guides_every_tool_that_needs_judgment` extended to all
nine agents — but the list is now produced by the review process instead of
maintained separately.

**Retire the standalone `_JUDGMENT_TOOLS` list** once its entries live in the
ledger. Two lists of the same thing is the dead-runbook defect, and we have paid
for that lesson twice already. Do this in the same PR, not "later."

### 2.3 Reverse join — no orphans

The ledger names only tools the agent actually has. A ledger entry for a removed
tool is a stale review record asserting something untrue.

Note the asymmetry deliberately: **roster growth fails; roster shrinkage does
not.** Removing a tool leaves a harmless extra sentence in a prompt. Adding one
leaves a capability nobody is told about. Only one of those is a defect, and the
guard should not pretend otherwise.

### 2.4 Plants — all three

1. Add a fake tool to an agent's `DEFAULT_AGENTS` roster without a ledger entry →
   **2.1 must go red.** This is the real-world case; if it stays green nothing
   here works.
2. Mark a tool `guided` whose name does not appear in that agent's prompt →
   **2.2 must go red.** Pick a tool whose name is not a substring of any word
   already in the prompt — §2.7 applies: a planted value that coincides with an
   expected output cannot redden its own branch, and prompt text is full of
   incidental substrings.
3. Add a ledger entry for a tool the agent doesn't have → **2.3 must go red.**

Verify each patch applied before reading its result.

---

## Step 3 — Wire it to the checklist

`design-note-prompt-drift.md` §5 is currently four questions with no enforcement.
Add a short §5.1 recording that question 1 now fails CI, and that questions 2–4
still rely on judgment because they cannot be mechanised: *can the capability
actually be delivered right now*, *does the tool say so when called*, and *does
anything fail if the prompt and the flag drift apart* are all facts about the
world, not about the repo.

Say plainly which one is guarded and which three are not. A design note that
implies more coverage than exists is the same failure it documents.

---

## §4 — The limit, stated in the module docstring

**The guard reads the seed, not production.**

Rosters are reconciled from seed by `seed_agents`, so the seed is the correct
trigger — roster growth is visible there. But **prompts are not reconciled**, so
the *fix* is partly outside CI: updating `DEFAULT_AGENTS[...].system` turns the
test green while production keeps its old prompt.

Two consequences, both to be written down rather than solved here:

1. **Green CI does not mean production is correct.** It means the seed was
   reviewed. The DB write is a separate, owner-authorised act.
2. **A prompt edited in production but not in seed is invisible to all three
   guards.** That is the inverse drift, and nothing here catches it. Name it as a
   known gap; do not build for it without a case.

---

## Step 5 — Report, don't act

Running 2.1 for the first time will list every tool added since each agent's row
was created. Expect a real backlog.

**For each one, report the disposition you propose and why — do not write to
production.** Prompt content reaching the live DB is an owner decision, and the
one already outstanding (`navigator` / `location_ping_log`) should be folded into
the same review rather than handled separately.

The PR should leave: the ledger populated, all three guards green against the
seed, and a list of agents whose production prompts still need the owner's write.

---

## Report back

Per step: what changed, the plant, whether it went red. For Step 1, report how many
tools you assessed and how many you marked `guided` — if that second number is
zero, say so loudly, because it almost certainly means the assessment was skipped
rather than that no tool needs guidance.

At the end: `alembic heads` unchanged, suite count, and the Step 5 list.
