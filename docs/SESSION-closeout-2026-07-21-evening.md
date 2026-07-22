# SESSION CLOSE-OUT — 2026-07-21 (evening)

**Session type:** Planning (browser Project) + Builder (Claude Code) across three
build orders.
**Companion document:** `SESSION-closeout-KEEL-V5-2026-07-21.md` (committed
`1627d52`) covers the KEEL arc from the same session. This one covers JARVIS.

---

## Shipped

| PR | What |
|---|---|
| **#41** | Morning brief: portfolio gather removed, `## Local network` added (Tailscale-only), section order pinned, Systems made exception-first |
| **#42** | Voice: numeric runtime-tunable `speechTimeout` replacing `"auto"` |
| `1627d52` | KEEL V5 close-out committed to `docs/` (docs-only, straight to main) |

Both PRs merged under merge-on-green with the fresh-Postgres migration gate
passing.

**Project tracking populated.** `backfill_projects.py` run with `--commit`
against production: 5 projects, 39 milestones, written through the tool path
(`build_registry` → projects handlers), never direct INSERT.

| Arc | Status | Done/Total |
|---|---|---|
| Location Pull Inversion | active | 5/8 |
| JARVIS Self-Health Loop | active | 8/11 |
| KEEL Curriculum | active | 2/7 |
| TDD Series (planning + scaffolding) | active | 3/6 |
| Duffel Live-Mode Activation | **parked** | 2/7 |

Duffel parked via `set_project_status` after creation, so the reason went through
the guarded path rather than being passed to `create_project`.

## Open — first thing next session

**The owner's test results are the starting point for the next session.** Three
things were left to test on real usage rather than asserted here:

1. **Complete milestone #30** — "Backfill current arcs into project tracking
   (TDD #1 step 6)" — by voice. Left open deliberately: a script cannot honestly
   mark itself done before it has run. It has now run.
   **Prediction to check:** she should say *"…4/6. Next: Build TDD #2
   planning-sessions."* Anything else is a finding.
2. **The #42 voice fix, on a real call.** Does a multi-clause request survive
   natural mid-sentence pauses now? Tune `voice_speech_timeout_seconds` from
   Admin (default 3, bounds 1–10) over a few days rather than redeploying.
3. **Compound / multi-agent requests.** Deliberately NOT investigated this
   session, because every prior test of it was invalid — `speechTimeout="auto"`
   was truncating utterances before the orchestrator ever saw them. Suggested
   probe: *"complete the backfill milestone on TDD Series, and tell me what's
   left on Location Pull Inversion."* Does she handle both clauses?

If she drops clauses, the plausible layers (in the four-layer order, none yet
investigated): orchestrator prompt does not establish that compound requests
exist → `_MAX_ITERS = 6` too tight for three delegations plus synthesis →
sequential sub-agent dispatch hitting the hold path. **Diagnose from logs before
changing anything.** If parallel dispatch is ever the answer, DB-bound calls stay
on the main thread for SQLAlchemy session safety — the same constraint the
briefing parallelization hit.

## Phone-side work — unchanged, still the critical path

Nothing in this session touched it. From `SESSION-closeout-2026-07-21.md`
(morning), in order:

1. **Tasker Event profile message filter → `^[A-Za-z0-9_-]{22}$`**, Use Regex
   **ON**, Exact Message **OFF**, Case Insensitive **OFF**. The message format
   became a bare nonce in PR #39; the old filter can never match.
2. Confirm a request flips to `fulfilled`.
3. Duplicate the task as the manual push — no profile, home-screen shortcut, no
   nonce, `"trigger":"manual"`.
4. Delete the old timed profile and task.
5. §8.1 diagnostic reverts: Force High Accuracy off, Continue Task After Error
   off, delete the `err=` flash, delete stray house-project tasks, battery
   Unrestricted, **Monitor intervals reverted bottom-up — they interlock**.
6. Export → scrub token → commit `devices/jarvis-location-pull.prj.xml` →
   **paste the real token back into Tasker after the scrubbed XML is committed.**

## Decisions taken

**R7 stays open.** The "morning-brief health section" milestone on Self-Health
Loop.

- *Claim considered:* PR #41 satisfied it.
- *Rejected.* R7 is defined by the 07-19 close-out as the brief consuming the
  **self-health check state** — the liveness / heartbeat / freshness / app-up
  results that `GET /api/status/full` and the status page render. #41 shipped Fly
  fleet status and Tailscale. Adjacent, not the same: `gather_context` still
  never reads check state.
- *Evidence:* the source list in `gather_context` after #41.
- *Consequence:* building R7 is its own small PR.

**Local network section ships Tailscale-only.**

- *Rejected:* wiring all three sources as originally specified.
- *Why:* `_get_node_status` and `_get_service_health` are Phase-1 stubs with no
  config gate and no unconfigured path — they return hardcoded fixtures
  including a fabricated `rpi-02: OFFLINE`. Wiring them would have put invented
  data at the head of an exception-first section, every morning.
- *Rejected also:* deferring the whole section. Tailscale is real and
  config-gated; discarding a working source because two others are not ready
  leaves no house coverage at all.
- *Evidence:* `netstatus.py` module docstring lines 4–6 and the fixture returns.
- *Revisit:* LAN migration, when all three become real.

**Voice speech timeout is a runtime setting, not a constant.**

- *Why:* the right value is discoverable only by real calls, and it is a genuine
  trade in both directions — too short truncates compound requests, too long
  makes every short answer wait on a timer. The owner will want to try 2, then 3.
  That is what the overlay is for.
- *Deferred:* `speechModel`. Verified as real live-API surface but not adopted
  this session; extending `_Key` to support string settings is its own change
  with its own validation story.

## Defects found, not yet fixed

**The `netstatus` agent will confidently fabricate a LAN outage.** The two stubs
above are reachable from the live agent roster, and the agent description
promises Proxmox and Kuma coverage. Asking her "is rpi-02 up?" today returns
`rpi-02: DOWN, 24-hour uptime 41 percent` — invented, unhedged, on every channel.

The briefing was one consumer and is now guarded. **The agent path is not.** Fix
is either a `[not configured]` sentinel from the stubs or an honest agent
description. This is a `project_hygiene`-class honesty defect, and it is also a
persona defect — see `TDD-persona-and-voice.md` §3.

## Learnings

**Write-only secrets are not unreadable.** Recovering a lost `DATABASE_URL` via
`fly ssh console -a jarvis-mdk -C "printenv DATABASE_URL"` printed the live
production Postgres password in plaintext into a chat log. **Credential rotated
immediately.** Fly secrets being write-only after `set` says nothing about
processes that hold the value in their environment. Use `fly proxy` plus a shell
variable set without echoing, or run the job where the value already lives. This
extends the existing rotation learning rather than replacing it.

**The stale-planner pattern, three for three.** Every planner error this session
was about *current repo state*, and every one was caught by the Builder reading
live bytes:

1. Three Admin defects reported open — two were already fixed, one
   (the "missing Ideas agent") was mis-specified: there is no Ideas agent by
   design, the ideas tools live on the secretary.
2. A `[not configured]` sentinel asserted across three netstatus handlers when
   two had no such path.
3. A whitespace difference in a *terminal paste* read as a code defect, with a
   recommendation to edit a file that was already correct.

The operating rule that falls out: **when a question's answer is "what does the
code currently do?", it goes to the Builder even when it looks like analysis.**
"Are these three defects still open" looks like a thinking task. It is a lookup.

**The stop-and-report clause earns its keep.** Both build orders carried "if
reality does not match this document, stop and report." It cost one paragraph and
caught the netstatus stubs before fabricated data reached the morning brief. Make
it standard boilerplate on every build order.

**The planner will try to build.** Given a repo snapshot, it edited working
source files directly and produced merge-ready output. Caught by the owner, not
by any control. Now written into the KEEL artifacts: *a planner's output is a
build order, not a merge.* Full account in the KEEL close-out.

## Documents added this session

- `docs/TDD-persona-and-voice.md` — placeholder. Emma Peel as the target
  register; §3 argues persona and correctness are the same problem where
  bluffing is concerned; §10 lists preconditions before it is scheduled (the
  netstatus honesty defect, SMS naturalness, TTS audition).
- `docs/SESSION-closeout-KEEL-V5-2026-07-21.md` — committed `1627d52`.
- `backfill_projects.py` — repo root. **Currently untracked.** It writes
  production rows and the arcs it seeds are themselves a record; `docs/operational/`
  is the better home. It already demonstrated the cost of being untracked once
  by not being where it was expected.

## Suggested next session

1. Owner's test results (above) — that is the opening item.
2. The `netstatus` honesty defect. Small, and it blocks the persona work.
3. R7 — brief consumes self-health check state.
4. Whatever the compound-request probe turns up.
