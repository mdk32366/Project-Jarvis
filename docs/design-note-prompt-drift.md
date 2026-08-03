# Design note — Prompt drift: rosters ship, prose doesn't

**Status:** Named mechanism + a decision rule, with the 2026-08-02 audit record.
**Prompted by:** the owner asking where in the UI he could see each agent's
prompt — which turned out to be two defects, one of them mine.

**Sibling notes:** `design-note-unwatched-instruments.md` (a *test* that can't
fail), `design-note-latch-failures.md` (a *system* that can't clear).

---

## 1. The mechanism

`seed_agents` **reconciles tool rosters and deliberately never overwrites
`system_prompt`.** That asymmetry is correct — an admin who tunes prose should
keep their wording, and the alternative silently destroys hand-tuning on every
deploy. But it has a consequence nobody was watching:

> **Every new tool reaches production. None of the prose explaining it does.**

A prompt is therefore frozen at whatever it said the day its row was first
inserted, while the roster grows underneath it indefinitely. Nothing surfaces
the gap: no error, no failing check, no user complaint — the agent simply
*doesn't offer* capabilities it has, which is indistinguishable from not having
them.

**Found by:** an owner who couldn't find the prompt editor in the UI. The
editor existed; the collapsed row never rendered the prompt, so nothing hinted
one was there (fixed in #66). Only after making it visible did anyone look at
what production actually held.

---

## 2. What the 2026-08-02 audit found

**All nine agents were still on their day-one seed.** Not one had ever been
edited — the mechanism had been running unopposed since each row was created.

| Agent | Prod | Seed | Gap |
|---|---:|---:|---|
| secretary | 537 | 3164 | 47 tools on the roster, guidance for ~5 |
| archivist | 136 | 663 | 1 of 8 tools mentioned; no correction or precedence rules |
| navigator | 314 | 477 | `where_am_i` and the phone-location default absent |
| netstatus | 215 | 269 | `tailscale_status` absent |
| travel | 416 | 494 | see §3 — the seed was the *worse* text |
| finance / infra / researcher / scheduling | — | — | in sync (never grew) |

The secretary is the clearest case: her prompt covered `draft_email`, tasks and
`capture_idea` while her roster had reached **47 tools** spanning projects,
planning sessions, inception, watches, callbacks and Google Docs. Every one of
those was reachable and none was explained.

---

## 3. The decision rule — and the trap in it

The obvious remedy is "sync production to the seed." **That is wrong as a
general rule, and following it would have shipped a regression.**

`travel`'s seed said: *"You cannot book yourself — return the offer_id(s) to the
orchestrator, which books behind the confirmation gate and a TOTP code."*
Architecturally accurate. But booking is gated twice — `booking_enabled`
(default `False`, a hard-refused stub) and a separate live Duffel key, with the
vendor side not activated — so that text points the agent at a hand-off toward a
booking that **cannot complete**. Production's older, quieter *"you cannot book;
offer to open a task"* was the truer sentence.

The frame "matches the seed" measures **difference, not correctness**:

> **The seed reflects what the code can do. The prompt should reflect what
> actually works.** Those diverge whenever a capability ships but is blocked
> downstream — a kill switch, a vendor gate, an unactivated account, a
> credential nobody has minted.

### The rule that resolves it

> **A prompt may name a capability the system cannot currently deliver — but
> only if the tool itself says so when called.**
> Where the failure is **silent**, the prompt must carry the warning.
> Where the failure is **self-announcing**, the prompt should just point at the
> tool.

Three cases from one audit, identical in the table above, resolved differently:

- **`travel` — silent failure. Do not sync.** Nothing between the user and a
  broken promise except the prompt.
- **`navigator` — self-reporting. Sync.** The seed claims location "defaults to
  where the user currently is"; `where_am_i` reports the fix's age
  (*"Last position 50 minutes ago"*) and falls back to home past
  `location_max_age_minutes`. The tool corrects any over-promise.
- **`netstatus` — self-announcing. Sync.** All three tools are unreachable
  (Proxmox and Kuma are on the LAN, `TAILSCALE_TAILNET` is unset), but each
  returns a plain `[not configured]` explanation. Naming a self-announcing tool
  beats silence: the agent relays *"needs TAILSCALE_TAILNET set"* instead of
  *"I can't check Tailscale"* from ignorance, which is actionable rather than
  merely discouraging.

---

## 4. What was done

Eight agents synced to seed by direct DB write (the Admin UI's own path;
`seed_agents` will neither re-apply nor undo it). **`travel` was not synced** —
instead the *seed* was amended to lead with the operational truth, so the two
converge on the honest text rather than the architectural one.

Two guards now exist, both negative-validated:

- `test_the_secretary_prompt_guides_every_tool_that_needs_judgment` — a curated
  list of tools whose schema leaves a real decision unmade, asserted present in
  the prompt. Curated rather than "every tool on the roster": most of the
  secretary's 47 are self-describing CRUD, and a prompt enumerating them all
  would be a manifest, not guidance. A companion test asserts the curated list
  only names tools she actually has — a guidance list that drifts from the
  roster is the dead-runbook defect in another costume.
- `test_the_travel_prompt_tracks_the_booking_kill_switch` — ties the prompt to
  `booking_enabled` in **both** directions. Enabling booking without updating
  the prompt fails CI, because that would leave the agent refusing a capability
  it now has: the mirror of the defect the guard was written for.

---

## 5. The checklist

When adding a tool to an agent's roster:

1. **Does its schema leave a real decision unmade?** When to reach for it
   unprompted; which of two similar options is right; what not to promise. If
   yes, it needs a line in the prompt — the roster alone will not carry it.
2. **Can the capability actually be delivered right now?** Shipped ≠ usable.
   Check the kill switches and the credentials, not just the code path.
3. **If not, does the tool say so when called?** That answers whether the prompt
   can name it (§3).
4. **Does anything fail if the prompt and the flag drift apart?** If not,
   consider tying them — a test that reads both is cheap and is the only thing
   that will notice.

### 5.1 One of those four is enforced. Three are not.

Since 2026-08-03 (`app/prompt_review.py`, `tests/test_prompt_review.py`):

| Question | Enforced? |
|---|---|
| 1. Does its schema leave a real decision unmade? | **YES — fails CI.** Every rostered tool must carry a disposition in the ledger, and every `guided` one must be named in that agent's prompt. |
| 2. Can the capability actually be delivered right now? | No. |
| 3. If not, does the tool say so when called? | No. |
| 4. Does anything fail if the prompt and the flag drift apart? | No — except `travel`, which has a bespoke tie to `booking_enabled`. |

**Stated plainly because a design note that implies more coverage than exists is
the same failure it documents.** 2–4 are facts about the *world* — a vendor's
activation state, what a tool prints at runtime, whether two things that should
move together actually do — not facts about the repo. CI cannot read them.
Question 1 is enforceable precisely because it is a repo fact: a roster grew.

**The guidance guard's green can also be earned trivially.** It passes when a
`guided` tool's *name* appears in the prompt — so a prompt that merely lists tool
names satisfies it while carrying no guidance at all: the manifest failure the
curated-list design was rejected to avoid. *"Is this real guidance"* is a
judgment, not a property of the repo, so it cannot be mechanised and is recorded
rather than solved (`app/prompt_review.py`, limit 3).

Found by the thing it describes: `set_project_status` was `guided` while the
prompt carried the parking rule and never named the tool — **guidance present,
tool unattached**, the agent reading a rule with nothing to bind it to. The fix
was to name the tool, not to downgrade the disposition. The inverse — a name with
no rule behind it — is what nothing catches.

**And the enforcement reads the SEED, not production.** Green CI means the seed
was reviewed, not that the live agent has the prose. The DB write remains an
owner-authorised act, and a prompt edited in production but not in seed is
invisible to all three guards — the inverse drift, named as a known gap.

And when auditing prompts: **a difference from the seed is a question, not a
finding.** Ask which text a user would rather have been told.

---

## 6. The one-line version

Rosters ship and prose doesn't, so every prompt rots quietly toward the day it
was written. When you go to fix it, remember the seed knows what the code can
do and nothing about what currently works.
