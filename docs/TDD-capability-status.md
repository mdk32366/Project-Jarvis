# TDD — Capability Status Rollup

**Written:** 2026-07-31, *after* the seed ratification, not before.
**Status:** v1 shipped.
**Seed ratified in:** `BUILD-ORDERS-2026-07-31-capability-seed-ratification.md`
**Draft it ratifies:** `DRAFT-capability-seed-for-ratification.md`

> **Provenance note, stated plainly.** The build orders reference this document as
> though it existed. It did not — not in this repo, not in the orders bundle. The
> seed draft was therefore built from the **live `component` table**, and this TDD
> was written afterwards to record what was actually built and why. Sections
> marked **DERIVED** were reasoned out during the build rather than handed down;
> they are the ones most worth a second opinion.

---

## 1. Why a capability layer at all

The status page answers *"is this part working."* Nobody asked that. They asked
*"can you still tell me where I am."*

`google_calendar_svcacct: down` is a true sentence that reads as noise. "Calendar:
red — the credential died four days ago" is the same fact, addressed to the person
who has to care. The rollup exists to close that gap, and nothing more: it invents
no new health signal, runs no new probe, and stores no new judgment. It reads
`health_result` and groups it.

## 2. What a capability is

A **capability** is what the owner would say JARVIS can *do*. A **component** is a
part of the system. Both are needed and neither substitutes:

| | Component | Capability |
|---|---|---|
| Question | is this part working? | can she still do the thing? |
| Example | `postgres: ok` | `Memory: ok` |
| Count | 29 | 8 live + 2 gated |
| Source of truth | a check | a rollup of checks |

## 3. The rules

```
red      primary member is `down`
amber    primary is `degraded`, OR any non-primary is `down`/`degraded`
unknown  primary is `unknown`
ok       primary ok, no non-primary fault
gated    lifecycle=gated — reported as not-configured, never counted
```

### 3.1 Primary

Each capability has **exactly one** primary: the member whose failure means the
capability is *broken* rather than *impaired*. Everything else is a contributor.

The morning brief is the clearest case. Lose `nws` and the brief still goes out,
minus its weather section — which PR #44 already made explicit ("never narrate an
absent section"). Lose `worker_scheduler` and no brief exists at all. One status
word for both would teach the reader to skip both.

### 3.2 **DERIVED** — primary `degraded` is amber, not red

The orders define primary as "the member whose **non-ok** forces red". Read
literally, a `degraded` primary would be red.

That cannot be right, because the same document ratifies the project-tracking
**amber ceiling** — and `project_hygiene` is `degraded`-or-better by design. A
literal reading would red project tracking on exactly the state the orders say
must be amber.

So: **`down` → red, `degraded` → amber**. Degraded means impaired, not broken.

### 3.3 **DERIVED** — a non-primary `unknown` does not move the rollup

`unknown` on a *primary* is `unknown` for the capability: no basis to judge.

`unknown` on a *secondary* is recorded and surfaced but does not change the
status. For a contributing member, absence of evidence is weaker than evidence of
failure — and `gmail` and `nws` both sit at `unknown` in production for the
entirely benign reason that nothing has called them recently. Counting that as
amber would paint the morning brief permanently amber, which is precisely how a
status surface teaches people to ignore it.

### 3.4 Trunk membership is explicit (decision 8)

`postgres`, `anthropic_api`, `worker_scheduler`, `email_ingest`, `health_evaluator`
are `blast_radius=multi`. They are members of a capability **only where genuinely
load-bearing** — `postgres` is primary for Memory, absent from Voice+SMS. Trunk
faults render *above* the rollup, not inside it. The alternative (implicit
membership everywhere) reports one Postgres outage as eight red capabilities.

## 4. The seed

### 4.1 Live (8)

| Capability | Primary | Other members |
|---|---|---|
| Location | `location_responsiveness` | `location_pull_scheduler`, `navigator` |
| Calendar | `google_calendar_svcacct` | `scheduling` |
| Morning brief | `worker_scheduler` | `gmail`, `twilio`, `nws`, `google_calendar_svcacct`, `google_maps` |
| Project tracking | `project_hygiene` | — |
| Memory | `postgres` | `archivist`, `anthropic_api` |
| Voice + SMS | `twilio` | — |
| Self-health | `health_evaluator` | `postgres`, `worker_scheduler` |
| Contacts / People | `google_oauth` | `secretary` |

Location's primary is **responsiveness**, not the scheduler, because it is the
end-to-end signal: a dead scheduler eventually shows up in responsiveness, while
the reverse is not true.

### 4.2 Gated (2)

| Capability | Gate | Why |
|---|---|---|
| Flight booking | `booking_enabled` (default `False`) | not switched on |
| Local network | none — being on the LAN is the gate | all four members are stubs, unreachable from Fly |

Gated capabilities are **reported as not-configured, never omitted.** Silent
absence is how a capability stops being noticed — the same argument PR #43 made
for netstatus.

The four LAN components are grouped as **one** capability rather than four gated
rows: they share one cause and one fix, so four rows would be one fact repeated
four times.

### 4.3 Documented ceilings

Two capabilities carry a permanent, deliberate limitation in their `notes`, so the
limitation travels with the reading instead of living in a close-out nobody
re-reads:

- **Project tracking has no red path.** `project_hygiene` never returns `down`;
  the tools are ungated DB reads/writes with no user-visible outage mode. A
  drifting project record is a bookkeeping problem. Manufacturing a red path for
  symmetry would put bookkeeping beside a dead scheduler.
- **Memory's vectorstore is uninstrumented.** A semantic-recall failure is
  invisible to this rollup — stored memory would read green while recall silently
  returned nothing. Pending a `vectorstore` component.

### 4.4 Voice + SMS is one capability, and why that is not laziness

Twilio voice and A2P SMS genuinely fail independently. But there is exactly **one**
`twilio` component covering both, so two capabilities would share one member,
always agree, and the split would be cosmetic — two rows that can never diverge is
the same false-attribution shape that retiring `location_pings` was meant to end.

Splitting the capability requires splitting the **component** first
(`twilio_voice` / `twilio_sms`, each with its own check and runbook). Separate work.

## 5. Data model (migration `0025_capability_rollup`)

| Table | Purpose |
|---|---|
| `capability` | name, label, lifecycle, gated_by, description, notes, enabled |
| `capability_member` | (capability, component) + `is_primary` |
| `evaluator_heartbeat` | single row: `beat_at`, `components_checked`, `duration_ms` |

**No seed data lives in the migration.** `seed_health_topology` reconciles
capabilities from code on every startup, exactly as it does components. A seed
frozen into a migration is stale reference data by construction — the bug the
reconciling seed exists to prevent.

Membership reconciles by **replacement**, not merge: a member dropped from the code
seed must actually disappear, or it keeps silently voting on a status.

## 6. F1 — `health_evaluator`, the check that must not be faked

Before this, the health system had no component representing *itself*.
"Self-health: ok" could only ever mean "the things health checks depend on are ok"
— never "health checking is running." An evaluator that silently stopped would
leave every stale reading, including every green, looking current.

That is the fabricated-`ok` failure of the pre-epoch `actions_audit`, one level up,
and it is the one thing in this build that must not be faked, because its job is
noticing when things are faked.

### 6.1 Mechanism

The worker calls `run_health_cycle` on an interval (`health_cycle_seconds`, 300s),
which runs every check and then stamps `evaluator_heartbeat`. The component's
check reads staleness against a seeded `stale_seconds` of **900** — three missed
cycles, so an ordinary slow tick never trips the alarm that means *nothing is being
checked*.

Throttled deliberately: the worker ticks every 5s, and a full cycle scans a 30-day
slice of `actions_audit` per liveness component. Per-tick evaluation would spend
the worker's life proving it is alive.

### 6.2 The trap, and how it is closed

`status_payload` runs the same checks but **does not stamp the heartbeat**. If
viewing the status page refreshed the beat, opening the page would prove the
evaluator alive *by the act of asking* — and a dead background evaluator would read
green to the only person looking at it. Pinned by
`test_status_page_does_not_stamp_the_evaluator_heartbeat`.

### 6.3 Two distinguished faults

| Fault | Means |
|---|---|
| `evaluator_stale` | nothing is recomputing; **every other reading on the page is suspect** |
| `rollup_incoherent` | it *is* running, but a capability names a component that is missing or disabled — a confident answer built on a part nobody checks |

Staleness wins: an incoherence report from a dead evaluator is itself stale.

## 7. F2 — `google_oauth` liveness

`google_oauth` used `check_type="published_expiry"` — a check that was **deferred
and never built**, because Google refresh tokens publish no expiry. It returned
`unknown` permanently, so any capability naming it as primary could never be green.

Moved to audit-derived `liveness`, the same shape every other external API uses.
Verified against production: 7 post-epoch audit rows, most recent
`secretary:google_status ok` — so it reads `ok`, and Contacts is genuinely
unblocked rather than nominally.

**Residual, stated:** with no Google call inside the liveness window it reads
`unknown` (no evidence). That is honest absence which usage clears — unlike a
permanent unknown that no usage could ever clear.

## 8. Surfaces

### 8.1 `GET /api/status/capabilities`

Auth-gated. Runs checks fresh, then rolls up — a capability page showing the last
cycle's answer is worse than no page. Contains no secrets: `depends_on` (which
names secret env vars) never reaches the payload, pinned by test.

Each non-ok capability carries its **driving member** and that member's stored
runbook, resolved through the existing `remediation` table. Never the capability's
own generic text; never improvised. A missing runbook degrades to `null`.

### 8.2 Morning-brief line

One line, count only, ambers and reds named inline, reds first. Detail stays on
the status page — the brief is read aloud.

**The scoped exception to exception-first:** an all-green rollup still says so,
every day. Every other brief section goes silent when it has nothing to report.
But a monitor that only speaks up on failure is indistinguishable from a monitor
that has stopped, and this is the one line whose job is to prove otherwise. It
costs six words.

## 9. Fixed in passing — a runbook that could never join

`check_liveness` emits fault code `call_failed`. The only runbooks for
`google_calendar_svcacct` and `google_oauth` were keyed to `auth_invalid` /
`token_expired` — codes **no check produces**. So the live, four-day-old calendar
outage rendered on the status page with no runbook at all.

Runbooks keyed to `call_failed` added for both. The aspirational codes are left in
place for when a check emits them. Pinned by
`test_the_live_calendar_fault_code_resolves_to_a_runbook`.

## 10. Known gaps

- **Vectorstore uninstrumented** (§4.3) — needs a `vectorstore` component.
- **Voice/SMS component split** (§4.4) — needed before the capability can split.
- **No UI.** The endpoint and brief line ship; the `/status` page does not yet
  render capabilities. Not ordered, not built.
- **Coverage.** Finance, Web research, and Infra are live, user-facing, and have
  no capability — ratified as a deliberate first slice (decision 7), not an
  oversight. `alpaca`, `finance`, `tavily`, `researcher`, `infra`, `email_ingest`
  belong to no capability today.
