# TDD — Backlog Item #12: JARVIS Self-Whoami (Identity, Provenance, Request Log, Health)

**Status:** Needs scoping decisions before build (see §8)
**Repo:** `mdk32366/Project-Jarvis`
**Prereq:** TDD #11 (datetime awareness) — this depends on `get_current_datetime`
for timestamping everything below. Build #11 first.
**Companion doc:** TDD-datetime-awareness.md — that one is "what time is it,"
this one is "what/who am I, and how am I doing."

---

## 1. What we're building

Every `whoami` in the system so far answers "who is the *owner*" — Matt's
name, DOB, phone, email, preferences. JARVIS has no equivalent answer for
**herself**: what code she's running, when she went into service, what she's
been asked to do and how each request was resolved, or whether the parts of
her that depend on external services are actually healthy.

This TDD adds a second `whoami` — call it `self_whoami` — plus the three
things underneath it: **provenance** (git-derived identity), a **durable
request/disposition log**, and a **health/expiry watch** over the
credentials she depends on.

**The scope is deliberately three separable pieces, not one blob.** They
share a motivation (JARVIS should be legible to herself and to Matt) but have
different write patterns, different consumers, and different urgency. Build
and test them as three phases, in the order below — each is independently
useful even if the others slip.

---

## 2. Root cause

- `whoami` (owner profile) has no counterpart for the system itself. Every
  fact about "what is JARVIS" currently lives in Matt's head, in scattered
  Fly secrets, or in git — never in a place JARVIS can query at runtime.
- There is no durable record of what JARVIS was asked to do and what she did
  about it. When something goes wrong (see: 4am wake-up call), the only
  post-mortem material is Matt's memory of the call and whatever's in
  application logs not designed for this purpose.
- API keys and tokens (Duffel, Twilio, Google OAuth, Tavily, Anthropic) have
  expiry or rotation windows that nothing currently watches. The first
  signal of expiry today is a failure at call time, surfaced to Matt as
  "JARVIS is broken" rather than "JARVIS's Twilio token expires in 4 days."

---

## 3. Phase 1 — Git provenance

The cheapest, lowest-risk piece. JARVIS should be able to answer "what
version of yourself are you" and "when did you first go into service."

### 3.1 What it returns

```python
{
    "current_commit": "a3f9c21",
    "current_commit_date": "2026-07-11T22:04:00-07:00",
    "current_commit_message": "Add TOTP second factor for booking",
    "deployed_at": "2026-07-12T08:15:00-07:00",     # from Fly.io deploy metadata
    "first_deploy_at": "2026-04-02T19:30:00-07:00", # earliest deploy on record
    "commits_since_first_deploy": 187,
    "repo": "mdk32366/Project-Jarvis",
    "days_in_service": 102,
}
```

### 3.2 Implementation

- `current_commit*` fields: read at container startup from `git rev-parse
  HEAD` and `git log -1 --format=%cI %s` — baked into the image at build
  time (Fly.io build args or a generated `version.json`), **not** a live git
  call at request time. A production container may not even have `.git`
  present depending on how the image is built; don't assume it does.
- `first_deploy_at` / `deployed_at`: Fly.io exposes release/deploy history
  via `flyctl releases` / the Machines API. Cache `first_deploy_at` once
  discovered (it shouldn't change) rather than re-querying the Fly API on
  every `self_whoami` call.
- This is read-only, no side effects, available everywhere — same
  ungated-and-universal treatment as `get_current_datetime`.

### 3.3 Tests

| Test | Property |
|---|---|
| `test_self_whoami_returns_current_commit_baked_at_build_time` | Not a live `git` shell-out at request time. |
| `test_self_whoami_does_not_crash_if_git_metadata_missing` | Degrades to "unknown" fields, never a 500, if the build didn't bake version info. |
| `test_first_deploy_at_is_cached_not_requeried` | One Fly API call ever (or on cache miss only), not per-request. |
| `test_days_in_service_computed_from_get_current_datetime` | Uses the Phase-1-dependency primitive from TDD #11, not `datetime.now()` directly. |

---

## 4. Phase 2 — Durable request/disposition log

This is the one that would have actually helped this morning: a place to
look and see "what did I ask her to do, and what did she think she did
about it."

### 4.1 What gets logged

Every top-level request that reaches the orchestrator (not sub-agent-internal
tool calls — this is request-level, not a full trace):

```python
{
    "request_id": "uuid",
    "received_at": "2026-07-13T04:00:00-07:00",
    "channel": "voice",                    # voice | sms | scheduled
    "trigger": "scheduled:wakeup_call",     # user-initiated request text, or a schedule ID
    "disposition": "error",                 # completed | error | gated_pending | gated_cancelled | refused
    "summary": "Wake-up call: schedule readback",
    "error_detail": "AttributeError in ...",  # present only if disposition == error
    "duration_ms": 4200,
}
```

**This is intentionally coarser than application logging.** It's not a
replacement for whatever logging already exists — it's a *purpose-built,
queryable table* answering "what has JARVIS been asked, and how did each
one resolve," so that both JARVIS and Matt can ask about it directly instead
of grepping logs.

### 4.2 Why this matters for the wake-up call incident specifically

With this in place, "what happened with my wake-up call" becomes a query
JARVIS can answer herself — "at 4:00am I received a scheduled wake-up-call
trigger, started the schedule readback, and errored out partway through" —
instead of Matt having to reconstruct it from memory and hope the app logs
have enough context. **This log does not fix the crash.** It makes the
crash debuggable and, eventually, self-reportable ("hey, that wake-up call
this morning failed — want me to look at why?").

### 4.3 Implementation

- New table, `RequestLog` (or similar) — Alembic migration following the
  `0001` dialect-guard convention established in the flight-booking work.
- Written by the orchestrator at two points per request: on receipt
  (`received_at`, `channel`, `trigger`) and on completion/failure
  (`disposition`, `duration_ms`, `error_detail`). This means a crashed
  request still has a row — receipt-time write must commit independently of
  how the request resolves, which likely means writing receipt in its own
  short transaction rather than batching it with the final update.
- Exposed via a `get_recent_requests(n, disposition_filter=None)` tool,
  available to JARVIS herself (so she can answer "what have you been up to"
  or "did the wake-up call actually fire") and to whatever Matt uses to
  query it directly if he wants a dashboard later — out of scope here.
- Retention: needs a decision (§8) — this will grow unboundedly otherwise.

### 4.4 Tests

| Test | Property |
|---|---|
| `test_request_log_row_created_on_receipt_before_processing` | A row exists even if the request crashes immediately after. |
| `test_crashed_request_is_logged_as_error_not_silently_dropped` | The wake-up-call scenario, directly: disposition ends up `error` with detail, not just... nothing. |
| `test_gated_pending_disposition_distinct_from_completed` | A booking sitting at "waiting for TOTP" isn't conflated with done or failed. |
| `test_receipt_write_commits_independently_of_final_update` | Kill the process between receipt and completion (simulate) — receipt row survives. |
| `test_get_recent_requests_filters_by_disposition` | Can ask specifically for errors. |
| `test_request_log_retention_policy_enforced` | Old rows actually get pruned/archived per whatever §8 decides. |

---

## 5. Phase 3 — Credential/API health watch

"Perpetual watch on all the API key expirations" — the piece that turns a
silent failure into an early warning.

### 5.1 What it watches

Every external dependency with a known or discoverable expiry:

| Credential | Expiry signal |
|---|---|
| `TOTP_SECRET` | N/A — doesn't expire, skip |
| `DUFFEL_LIVE_API_KEY` / test key | Duffel doesn't publish hard expiry on API keys typically — verify; may just need a liveness ping instead |
| Twilio auth token | Doesn't auto-expire, but A2P registration status can change — check registration status, not a token expiry |
| Google OAuth (Contacts, Tasks) | Refresh token can be revoked; access token expiry is short-lived and self-renewing — watch for *refresh failures*, not access-token TTL |
| Tavily API key | Check their dashboard/API for expiry or quota exhaustion signal |
| Anthropic API key | No published expiry; watch for auth-failure responses as the signal instead |

**Scoping correction from how this was originally framed (and a correction
to that correction):** most of these services don't expose a queryable
"expires in N days" via their own API — that's still true. But Matt's
right that this isn't the only signal available: **Fly.io already
timestamps every secret at creation/update** (`fly secrets list` shows a
`CREATED AT` / digest-change date per secret, per standard `flyctl`
behavior — worth confirming the exact field name against your Fly CLI
version, but the data exists and costs nothing to read). That's not a
service-published expiry, but it's real, already-tracked, zero-additional-
infrastructure signal: **age since last rotation.** A credential that's
400 days old with no rotation is worth surfacing even if nothing has
failed yet — a genuinely earlier warning than "wait for a call to fail" —
and JARVIS doesn't have to build or maintain anything new to get it; Fly
already holds it.

So this phase has **two independent signal types**, not one:

1. **Liveness** — did the last real call to a service succeed. Always
   available, for every tracked service, built as described below.
2. **Age-since-rotation** — pulled from `fly secrets list` metadata, free,
   and a leading indicator liveness can't give you (nothing has to fail
   first).

A service-published expiry (OAuth refresh tokens) remains a third,
strongest category where it genuinely applies. Don't fabricate a countdown
for services that don't offer one — but age-since-rotation is not
fabricated, it's real data Fly already holds, and leaving it out of the
original framing was wrong.

### 5.2 What it returns

```python
{
    "checked_at": "2026-07-13T07:42:00-07:00",
    "services": [
        {
            "name": "duffel",
            "status": "ok",
            "last_success_at": "2026-07-13T07:00:00-07:00",
            "last_failure_at": None,
            "expiry_known": False,
        },
        {
            "name": "google_oauth",
            "status": "ok",
            "last_success_at": "2026-07-13T06:55:00-07:00",
            "last_failure_at": None,
            "expiry_known": True,
            "expires_at": "2026-09-01T00:00:00-07:00",
        },
        {
            "name": "twilio",
            "status": "degraded",
            "last_success_at": "2026-07-10T14:00:00-07:00",
            "last_failure_at": "2026-07-13T04:00:02-07:00",
            "note": "A2P registration status: in review",
        },
    ],
    "secret_age": [
        {"name": "DUFFEL_LIVE_API_KEY", "created_or_updated_at": "2026-06-01T00:00:00-07:00", "age_days": 42},
        {"name": "TWILIO_AUTH_TOKEN", "created_or_updated_at": "2025-04-02T00:00:00-07:00", "age_days": 467},
    ],
}
```

`secret_age` is pulled straight from `fly secrets list`, not inferred or
guessed — one field per secret, no per-service custom logic needed. A
sensible default threshold (e.g., flag anything over 365 days as "consider
rotating") is a reasonable v1; make it configurable rather than hardcoded
per-service, since "how stale is too stale" varies by credential and isn't
something to hardcode per name.

Note `twilio` in that example — a failure at exactly `04:00:02` on the same
morning as the wake-up call is the kind of correlation this phase exists to
surface. **Whether that's actually what happened is unknown right now** —
this is illustrative of what the system should catch, not a claim about
today's incident.

### 5.3 Implementation

- Passive-first: piggyback on real call outcomes. Every outbound call to a
  tracked external service updates `last_success_at`/`last_failure_at` for
  that service — no separate polling infrastructure needed for services
  that get called often enough (Duffel, Twilio, Google, Tavily, Anthropic
  itself).
- Active check only where passive data would be too sparse to be useful
  (a rarely-called service could go stale for a long time before anyone
  notices) — a lightweight scheduled liveness ping, low frequency (daily is
  plenty), not a tight polling loop.
- `secret_age` pulled via `flyctl secrets list --app jarvis-mdk --json` (or
  the Fly GraphQL/Machines API equivalent if shelling out to `flyctl` from
  inside the running container is awkward — worth checking whether the
  container has `flyctl` available or needs the API called directly with a
  Fly API token). Cache with a short TTL (hourly is plenty) — this doesn't
  change fast and doesn't need to be queried per-request.
- Surfaced via `self_whoami` (rolled up) and a dedicated
  `get_service_health()` tool for a direct check.
- **Proactive alerting is a separate decision** (§8) — should JARVIS ever
  volunteer "hey, Twilio's been failing since 4am" unprompted, or only
  answer when asked? Recommend: answer-when-asked first, add proactive
  alerting later once the passive data has been observed for a while and
  false-positive rate is known.

### 5.4 Tests

| Test | Property |
|---|---|
| `test_passive_health_updates_on_real_call_success` | A normal Duffel search call updates `last_success_at` without extra plumbing per call site — should be a decorator/wrapper, not manual bookkeeping in every handler. |
| `test_passive_health_updates_on_real_call_failure` | Same, for failures — captures the error, doesn't swallow it. |
| `test_health_check_never_blocks_the_calling_request` | A health-check side effect must not add latency or a failure mode to the actual user-facing call it's piggybacking on. |
| `test_expiry_countdown_only_shown_where_service_publishes_one` | Duffel/Twilio/Anthropic don't get a fabricated countdown; OAuth does. |
| `test_get_service_health_reflects_most_recent_known_state` | No stale cache masking a recent failure. |
| `test_self_whoami_rolls_up_service_health_summary` | The combined `self_whoami` call surfaces a health summary, not just identity/provenance. |
| `test_secret_age_pulled_from_fly_metadata_not_fabricated` | `secret_age` values trace to real `fly secrets list` timestamps, never invented or estimated. |
| `test_secret_age_cached_with_short_ttl_not_per_request` | Fly API/`flyctl` isn't invoked on every `self_whoami` call. |
| `test_stale_secret_flagged_past_configurable_threshold` | A secret older than the configured threshold is flagged; a fresh one isn't. |

---

## 5.5 Phase 4 — Multi-repo provenance ("the head, watching the limbs")

Phase 1 gives JARVIS provenance over *her own* repo. Matt's framing for this
one is worth keeping verbatim because it's the right mental model: **the
orchestrator is the head; each hosted app/repo is a limb; "my arm hurts, my
leg hurts" is what a limb reports up, not something the head has to
independently rediscover every time.** This phase extends git-provenance
awareness from "JARVIS knows her own version" to "JARVIS knows the state of
everything she's meant to be operating," across however many repos/hosts
that turns out to be.

This is explicitly the most speculative phase — it depends entirely on how
many other hosted apps exist, where they live, and whether JARVIS has (or
should have) any credentials to reach them. **Do not build this until
that inventory exists.** What follows is a shape, not a spec.

### 5.5.1 What it would need to know, per watched repo/app

- Repo identity (`owner/name`, host — GitHub, elsewhere)
- Last commit (hash, date, message) — same shape as Phase 1's self-record
- Deploy status if discoverable (Fly app, or whatever else is hosting it)
- Basic liveness (is it responding), reusing Phase 3's health-check pattern
  rather than inventing a second one

### 5.5.2 Access model — the actual open question

This is the part that needs a real decision before any code, not a TDD
default: **does JARVIS get read access to other repos/hosts, and if so,
how scoped?** A few shapes, roughly in order of how much I'd want to see
justified before going further than it:

- **Read-only, GitHub API, scoped to specific named repos** — lowest risk,
  answers "what's the latest commit on X" without JARVIS holding any
  credential that could do anything else. Start here if this gets built at
  all.
- **Read-only, extended to other Fly apps' deploy metadata** — same
  read-only posture, one more data source (deploy history, not just git
  history).
- **Anything beyond read** (triggering redeploys, restarting a service) is
  a different, much bigger conversation — that's an action with blast
  radius on infrastructure Matt didn't build JARVIS to control, and it
  shouldn't sneak in under "self-awareness." If this ever comes up, treat
  it with at least the scrutiny the booking gate got, not less.

### 5.5.3 Tests (once scoped)

Genuinely can't write a meaningful test table yet — it depends entirely on
§5.5.2's access-model decision and the actual list of repos/hosts involved.
Placeholder categories once that's settled: per-repo read succeeds/fails
gracefully, credentials are scoped no wider than declared, a limb being
unreachable degrades to "unknown," not a crash in the head's own status
report.

---

## 6. `self_whoami` — the combined tool

```python
def register_self_whoami(reg: Registry) -> None:
    reg.register(
        {
            "name": "self_whoami",
            "description": (
                "Get JARVIS's own identity, provenance, and health status: "
                "current code version, when she went into service, recent "
                "request history summary, and the health of external "
                "services she depends on. Use when asked about her own "
                "status, uptime, recent activity, or whether something is "
                "broken."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _self_whoami,
        gated=False,
    )
```

Composes Phase 1 (provenance) + a summary rollup of Phase 2 (e.g., "3
requests in the last 24h, 1 error") + Phase 3 (service health). Available
everywhere, same as `get_current_datetime` — read-only, no reason to gate.

---

## 7. Things I would push back on, if asked

- **Don't fabricate a countdown for credentials that don't publish one** —
  that part of the original pushback stands. But don't throw out
  age-since-rotation either: Fly already timestamps every secret, so "how
  long since this was last rotated" is real, free data, not a fabrication.
  The honest version of this phase is liveness + real Fly-sourced age +
  true expiry where a service actually publishes one — three tiers of
  signal, not one.
- **Don't let Phase 2's request log become a full trace/observability
  system.** It's one row per top-level request, coarse and queryable by
  JARVIS herself in conversation — not a replacement for real logging or
  APM. If you want that, that's a different, bigger project.
- **Don't add proactive alerting in the first cut.** "JARVIS texts me
  unprompted when something looks degraded" is a good eventual feature and
  a bad first version — you'll get paged by false positives before the
  health-check logic has been observed long enough to trust. Land passive
  observation first, alert later.
- **Don't make git provenance a live shell-out.** Bake it at build time.
  A production container doing `git log` on every `self_whoami` call is
  fragile (may not have `.git`) and pointlessly slow for something that
  doesn't change between deploys.
- **Build in the order given — Phase 1, then 2, then 3.** Phase 1 is nearly
  free and immediately useful for "what version are you running." Phase 2
  is what actually would've helped debug this morning. Phase 3 is real and
  now has a genuinely free data source (Fly secret age) alongside liveness.
  Phase 4 is not "next" — it's parked until there's an actual inventory of
  other hosted apps and a real decision on access model. Don't build
  cross-repo credentials speculatively.

---

## 8. Open decisions — needs your call before build

Unlike the flight-booking TDD, this one has real open questions rather than
settled positions, because it's new territory rather than closing a known
gap:

1. **Request log retention.** Unbounded growth otherwise. Options: fixed
   row count, time-based (e.g., 90 days), or size-based rollup/archive.
   No strong recommendation yet — depends on how chatty the log turns out
   to be in practice.
2. **Does `self_whoami` ever get called proactively** (e.g., JARVIS
   mentions "by the way, Twilio's been flaky" unprompted), or strictly
   on-demand for now? Recommend on-demand-only for v1 (see §7).
3. **Where does Phase 3's active liveness ping live** — a Fly.io scheduled
   machine, an in-process background task, or piggybacked on an existing
   cron-like mechanism if one already exists in the codebase? Need to know
   what's already there before picking.
4. **Does the wake-up-call crash get its own ticket now**, tracked
   separately from both this and TDD #11? Recommend yes — see §0 of
   TDD-datetime-awareness.md. Worth doing before or alongside Phase 2 here,
   since Phase 2's request log is exactly the tool that would make that
   crash's root cause easy to find next time, but the crash itself is a
   bug in the scheduled-call handler, not a gap in self-whoami.
5. **Phase 4 (multi-repo provenance):** what other hosted apps actually
   exist, and what access model (see §5.5.2) is acceptable for JARVIS to
   have into them? This needs an inventory and a decision, not a default —
   flagged here so it doesn't get built ahead of that decision existing.

---

*Phase 1 is cheap and should ship fast. Phase 2 is the one with the most
direct payoff against this morning's incident. Phase 3 is real but should
be built expecting to reshape it once you see what "health" actually looks
like from passive data — resist the urge to over-design the alerting logic
before you have real signal to alert on.*
