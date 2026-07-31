# DRAFT — Capability Seed for Owner Ratification

**Status:** DRAFT. Not to be merged. Step 0 of the capability build (`TDD-capability-status.md`).
**Date:** 2026-07-31
**Built from:** the **live** `component` table on `jarvis-mdk` (28 rows), not from the TDD text.

> `TDD-capability-status.md` **does not exist in this repo or in the orders bundle.**
> This draft is built from the live inventory plus the §4.3 semantics quoted in the
> build orders ("primary = the member whose non-ok forces **red** rather than **amber**").
> If the TDD carries a different schema for `capability` / `capability_member`, the
> *mapping judgment* below still stands but the field names will need translating.

---

## 0. Live inventory this was built from

28 components. 15 carry a check; 13 are `check_type="none"` (reference topology only,
never a health signal). **A member with no check can never move a rollup** — that
matters for several seeds below.

| Live status, 2026-07-31 15:4x UTC | |
|---|---|
| `ok` | `postgres`, `worker_scheduler`, `google_maps`, `location_pull_scheduler`, `tavily`, `twilio` |
| `degraded` | `project_hygiene` |
| `down` | **`google_calendar_svcacct`**, `location_responsiveness` |
| `unknown` | `anthropic_api`, `alpaca`, `duffel`, `gmail`, `google_oauth`, `nws` |

---

## 1. Proposed seed

`P` marks the **primary** — non-ok forces red. Non-primary non-ok forces amber.

### 1.1 Location
| Member | Check | Now |
|---|---|---|
| **`location_responsiveness`** **(P)** | `location_responsiveness` | `down` |
| `location_pull_scheduler` | `location_scheduler` | `ok` |
| `navigator` | none | — |

Primary is responsiveness because it is the **end-to-end** signal: it scores request
fulfilment, so a dead scheduler eventually shows up here too, while the reverse is not
true. `navigator` carries no check and is a topology member only.

*Currently `down` and climbing* — 1 fulfilled of the 5-of-6 it needs after today's fix.
Seeding this capability red today is **correct**, and watching it go green on its own is
the §7 verification signal. Do not special-case it.

### 1.2 Calendar
| Member | Check | Now |
|---|---|---|
| **`google_calendar_svcacct`** **(P)** | `liveness` | **`down`** |
| `scheduling` | none | — |

⚠️ **This capability seeds RED on day one, and correctly so.** `calendar_lookup` has been
failing with `invalid_grant: Token has been expired or revoked` since at least 2026-07-27
(latest 2026-07-30 11:01 UTC). That is a real, days-old outage that no current surface
makes obvious — which is a fair argument that the rollup is worth building.

**Decision needed:** fix the credential first, or ship the rollup and let it report the
outage it was built to catch. Recommend the latter — a monitor whose first act is to find
a live fault is a monitor that works.

### 1.3 Morning brief
| Member | Check | Now |
|---|---|---|
| **`worker_scheduler`** **(P)** | `heartbeat` | `ok` |
| `gmail` | `liveness` | `unknown` |
| `twilio` | `liveness` | `ok` |
| `nws` | `liveness` | `unknown` |
| `google_calendar_svcacct` | `liveness` | **`down`** |
| `google_maps` | `liveness` | `ok` |

The cleanest illustration of the primary concept in the whole seed. If the scheduler
does not fire there is **no brief** — red. If a content source fails the brief still
goes out with that section absent, which PR #44 made explicit ("never narrate an absent
section") — amber. The graceful-degradation behaviour is already built; this just
names it.

Note this capability would read amber *today* on the calendar fault alone.

### 1.4 Self-health monitoring
| Member | Check | Now |
|---|---|---|
| **`postgres`** **(P)** | `liveness` | `ok` |
| `worker_scheduler` | `heartbeat` | `ok` |

⚠️ **FLAG — this capability has no component representing itself.** The health system is
the observer; there is no row for `app/health_checks.py` or `/api/status/full`. As seeded,
"self-health monitoring is ok" means "the things health checks depend on are ok", not
"health checking is actually running". A check that silently stopped evaluating would
still read green here. That is precisely the fabricated-`ok` failure mode of the
pre-epoch `actions_audit` — the same class, one level up.

The orders call the meta-check "mechanical once the seed is ratified". It is not
mechanical if there is nothing in the topology for it to attach to. **Recommend adding a
`health_evaluator` component before this capability is seeded.**

### 1.5 Project tracking
| Member | Check | Now |
|---|---|---|
| `project_hygiene` | `project_hygiene` | `degraded` |
| **primary — UNRESOLVED** | | |

**Correction to the orders:** PR #40 is **`MERGED`**, not "still green, still unmerged"
(the 07-31 close-out §8 is stale on this). Project tracking is live in production —
5 projects, 39 milestones. So the suggestion to stage it `gated`/omitted *pending #40*
does not apply; #40 has landed.

**The amber-ceiling question stands, and it is real.** `project_hygiene` is
informational and never returns `down` — by deliberate design. So with it as primary,
this capability can never be red. Three options:

1. **Accept the ceiling.** Honest: no project-record inconsistency is a system outage.
   Project tracking genuinely cannot "go down" in a user-visible way — the tools are
   ungated DB reads/writes.
2. **Add `postgres` as primary.** Gives a real red path (no DB → no project tracking),
   but that is true of *every* capability and makes the red meaningless here.
3. **Omit from the live rollup** until it has a failure mode worth paging on.

**Recommend (1), explicitly documented.** A capability whose worst honest state is amber
should be allowed to say so, rather than have a red path manufactured for symmetry.
This is the same principle as `location_responsiveness` never returning green on <3
samples: the ceiling *is* the signal.

### 1.6 Contacts / People
| Member | Check | Now |
|---|---|---|
| **`google_oauth`** **(P)** | `published_expiry` | **`unknown`** |
| `secretary` | none | — |

⚠️ **FLAG — this capability can never be green as seeded.** `google_oauth` uses
`check_type="published_expiry"`, and published-expiry was **deferred, not built** (Google
refresh tokens publish no expiry) — so it returns `unknown` permanently. With it as
primary, and `unknown` correctly treated as not-green, Contacts reads perpetually
unknown.

This is the mirror of the project-tracking ceiling: that one can never be red, this one
can never be green. Both are seed-time detectable and neither is visible from the TDD text
alone. **Recommend either giving `google_oauth` a real liveness check (a cheap authed
call, same shape as the other `liveness` APIs) or omitting Contacts from the first
rollup** — a permanently-unknown capability trains people to ignore the panel.

### 1.7 Memory
| Member | Check | Now |
|---|---|---|
| **`postgres`** **(P)** | `liveness` | `ok` |
| `archivist` | none | — |
| `anthropic_api` | `liveness` | `unknown` |

`anthropic_api` is a non-primary member because distillation and recall degrade without
it while stored memory stays readable. FLAG: **no component row exists for the
embedding / vector store** (`app/embeddings.py`, `app/vectorstore.py`), so semantic recall
failing is invisible to this rollup. Same omission class as 1.4.

### 1.8 Voice + SMS
| Member | Check | Now |
|---|---|---|
| **`twilio`** **(P)** | `liveness` | `ok` |

**The orders ask whether this should be one capability or two. As the topology stands
today it cannot meaningfully be two.** There is exactly one `twilio` component
("SMS + voice"), so two capabilities would have identical single-member sets and could
never diverge — the split would be cosmetic.

The divergent-failure seam the orders correctly identify (Twilio voice vs A2P SMS
registration) is real, but it lives **one level down**: to split the capability you must
first split the component into `twilio_voice` and `twilio_sms`, each with its own check
and runbook. That is a topology change, not a seed change.

**Recommend: keep as one capability now; treat the component split as its own small
piece of work.** Splitting the capability without splitting the component would produce
two rows that always agree — exactly the false-attribution shape that retiring
`location_pings` was meant to end.

### 1.9 Flight booking — GATED, not a live member
| Member | Check | Now |
|---|---|---|
| **`duffel`** **(P)** | `liveness` | `unknown` |
| `travel` | none | — |

`gated_by = BOOKING_ENABLED` — confirmed as `settings.booking_enabled`, `config.py:291`,
**default `False`**. Staged as `gated`, excluded from the live rollup, per orders.

---

## 2. Coverage gap — the 8 capabilities do not cover the inventory

Components belonging to **no** proposed capability:

| Component | Suggests a missing capability |
|---|---|
| `alpaca`, `finance` | **Finance / portfolio** — live, ungated |
| `tavily`, `researcher` | **Web research** — live, ungated |
| `infra` | **Infra / Fly management** — live |
| `netstatus`, `proxmox`, `tailscale`, `uptime_kuma` | **Local network** — all stubs, unreachable from Fly |
| `email_ingest` | inbound email — arguably part of Voice+SMS or its own |

Finance and Web research are working, user-facing capabilities absent from the list of 8.
The four LAN components are stubs and should probably seed as `gated` (like flight
booking) rather than be omitted silently — an honest "not configured" beats invisibility,
which is the same argument PR #43 made for netstatus.

**Decision needed:** is the list of 8 deliberately a first slice, or is it incomplete?

---

## 3. Cross-cutting question the orders do not settle

**Are trunk components (`blast_radius=multi`) implicit members of every capability?**

`postgres`, `anthropic_api`, `worker_scheduler`, `email_ingest` are marked `multi`
precisely because a failure there takes down many limbs. Two readings:

- **Implicit** — every capability inherits them, so a Postgres outage reds everything.
  Accurate, but the panel becomes one fault repeated eight times.
- **Explicit only** — a capability lists a trunk member only where it is genuinely
  load-bearing (as done above). Reads better; risks a trunk fault showing as a puzzling
  set of unrelated ambers.

**Recommend explicit-only, plus rendering trunk faults *above* the capability rollup**
rather than inside it — which is what `blast_radius=multi` already exists to express, and
what the existing `/status` page already does.

---

## 4. Summary of what needs owner decisions

| # | Decision | Recommendation |
|---|---|---|
| 1 | Calendar seeds red on a live 4-day outage — fix first or ship? | Ship; let it report |
| 2 | Project tracking amber-ceiling | Accept and document |
| 3 | Contacts permanently `unknown` | Give `google_oauth` a liveness check, or omit |
| 4 | Voice+SMS one or two | One — split the component first |
| 5 | Self-health has no self component | Add `health_evaluator` before seeding |
| 6 | Memory has no vectorstore component | Add, or accept the blind spot |
| 7 | List of 8 omits Finance / Research / Infra / LAN | Confirm first-slice vs incomplete |
| 8 | Trunk implicit or explicit | Explicit; render trunk above the rollup |

Nothing downstream (evaluator, runbook resolution, endpoint, brief line, meta-check)
should be built until 1–8 are settled — every one of them changes what the rollup means.
