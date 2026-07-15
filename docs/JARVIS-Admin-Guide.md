# JARVIS Admin Guide

**Status:** SCAFFOLD for review. Structure + template + two fully-worked
chapters + roadmap table are complete; remaining chapters are stubbed with their
headings so the shape is visible. Once the structure is approved, the stubs get
filled in from the corresponding TDDs and code.

**Audience:** the administrator (Matt) — the person who deploys, configures,
tests, and repairs JARVIS. Not the end user (that's the forthcoming User Guide,
which will live as a page in the web app).

**Companion documents:**
- `docs/jarvis-test-plan.md` — the Test Guide: every test function and what it
  asserts. This guide says what each component *should* do; the test plan says
  what's *verified*. Use them together for UAT.
- The `TDD-*.md` set — the design records. This guide *distills* them into
  operational form; the TDDs remain the authoritative design history.

**How to use this for UAT:** go component by component. For each, read the
"Functional spec" and "Capabilities / Can't do" sections, then run the listed
UAT checks against the live system. This replaces scattershot testing with a
clean per-agent walkthrough.

---

## Chapter template (every component chapter follows this)

Each component (orchestrator, each sub-agent, each channel, each cross-cutting
subsystem) gets a chapter with these fixed sections:

1. **One-line purpose** — what this component is for.
2. **Functional spec** — what it does, described at a level you can test against.
3. **Design vs. design intent** — how it's actually built, and *why* it was
   built that way (the intent behind the design, including tradeoffs and
   rejected alternatives). This is where the "why" lives so it isn't lost.
4. **Capabilities** — the concrete things it can do, tool by tool.
5. **What it can't do** — explicit boundaries, and whether each is a deliberate
   design choice or a not-yet-built gap. (Prevents "is this broken or
   by-design?" confusion — the exact issue behind the "can't attach doc to
   email" episode.)
6. **Dependencies** — which external APIs / secrets / other components it needs.
   Cross-references the health-loop `component` table (TDD-jarvis-self-health-loop
   §4.1).
7. **Failure modes & remediation** — how it fails, the fault codes, and the
   runbook. Mirrors the `remediation` table so this guide and the DB agree.
8. **UAT checks** — a concrete checklist to verify the component works, at a
   functional-spec level. This is what you run during acceptance testing.
9. **TDD reference** — which TDD(s) govern this component.

---

## Part I — Architecture overview

### 1. System topology
The orchestrator + 9 sub-agents + external APIs + trunk subsystems. (Reference
the topology diagram; this is the map of everything the guide documents.)

### 2. The orchestration model
How the orchestrator routes to sub-agents, the delegation mechanism, and the
confirmation gate. The four gated actions (book flight, place trade, send email,
create calendar event) and why they live at the orchestrator level.

### 3. Data & record-keeping model
The four record-keeping layers (stdout / actions_audit / request log /
health_result — see TDD-jarvis-self-health-loop §4A) and the relational model
(component / remediation / health_result). What's deterministic and lives in
Postgres vs. what's transient.

### 4. Channels
Email (IMAP/SMTP), SMS (Twilio + A2P status), Voice (Twilio TwiML), Web chat
(React SPA). Whitelist enforcement across all four.

---

## Part II — Component chapters (one per agent + cross-cutting subsystems)

*Two chapters below are fully worked as the reference pattern. The rest are
stubbed.*

---

### 5. Agent: `scheduling` — Google Calendar  *(FULLY WORKED — reference chapter)*

**1. One-line purpose.** Reads the owner's Google Calendar so JARVIS can answer
"what's on today," "am I free Thursday," and feed calendar context into the
morning brief.

**2. Functional spec.** Given a natural-language time reference ("today," "this
week," "next Tuesday"), the scheduling agent resolves it to a date range and
returns the events in that range. It is read-only: it surfaces events but does
not create, move, or delete them. Event *creation* is a separate, gated action
performed by the orchestrator, not this agent.

**3. Design vs. design intent.**
- *Design:* The agent holds a single tool, `calendar_lookup`. Auth is via a
  **Google service account** (`GOOGLE_SERVICE_ACCOUNT_JSON`), distinct from the
  OAuth path the rest of Google uses. The target calendar must be *shared with*
  the service account's email, and the service account must hold the
  `calendar` scope.
- *Intent:* A service account was chosen for calendar so JARVIS can read the
  calendar unattended (no interactive re-consent), which suits a background
  system that pulls the calendar every morning without a human present. The
  tradeoff — and a known source of confusion — is that this makes calendar a
  *second* Google auth mechanism alongside OAuth (Contacts/Tasks/Docs/Sheets).
  A candidate future simplification is consolidating calendar onto the same
  OAuth path so there is one Google auth to reason about (see roadmap).
- *Rejected alternative:* using OAuth for calendar too would unify auth but
  reintroduces interactive-consent fragility for an unattended reader.

**4. Capabilities.**
- `calendar_lookup` — resolve a relative/absolute date range and return events.

**5. What it can't do.**
- **Cannot create/edit/delete events** — *deliberate*. Creation is an
  orchestrator-level gated action (like sending email), so an irreversible
  calendar write always passes the confirmation gate.
- **Cannot read a calendar not shared with the service account** — *deliberate*
  auth boundary. If a calendar isn't shared, it's invisible.

**6. Dependencies.** `GOOGLE_SERVICE_ACCOUNT_JSON` secret; the target calendar
shared with the service-account email; `google_calendar_svcacct` component in
the health model.

**7. Failure modes & remediation.**
- `auth_invalid` (the "invalid scope" seen in the 4 AM brief) → service account
  lost calendar scope, or the calendar isn't shared with it. **Runbook:**
  re-share the calendar with the service-account email; verify the `calendar`
  scope in `scheduling.py`. Verify via `GET /api/calendar/health`.
- `not_configured` → `GOOGLE_SERVICE_ACCOUNT_JSON` unset. **Runbook:** set the
  secret.

**8. UAT checks.**
- [ ] Ask "what's on my calendar today" → returns today's events (or a clean
  "nothing scheduled").
- [ ] Ask "am I free Thursday afternoon" → resolves the relative date correctly.
- [ ] `GET /api/calendar/health` → healthy.
- [ ] Confirm the morning brief includes calendar events (end-to-end).
- [ ] Ask JARVIS to *create* an event → routed to the gated orchestrator path,
  not silently done by the agent.

**9. TDD reference.** README capabilities table; datetime-awareness TDD (relative
date resolution); self-health-loop TDD (the `auth_invalid` remediation row).

---

### 6. Agent: `travel` — flight search & booking  *(FULLY WORKED — reference chapter)*

**1. One-line purpose.** Searches real flight offers (Duffel) and surfaces the
owner's booked trips; booking itself is a gated, TOTP-protected orchestrator
action, never done by this agent.

**2. Functional spec.** Given an origin, destination, dates, and trip shape
(one-way / round-trip / open-jaw), returns real fares, times, and carriers, each
with an `offer_id`. Separately, lists the owner's booked trips (learned from
airline confirmation emails, so no airline credentials are held). Returns
`offer_id`s to the orchestrator; the orchestrator books behind the confirmation
gate plus a TOTP second factor.

**3. Design vs. design intent.**
- *Design:* Two read tools — `search_flights` (Duffel API) and `list_trips`
  (from parsed confirmation emails). Booking is `book_flight`, registered
  `gated=True` at the orchestrator level with a TOTP second factor. Only offers
  retrieved by `search_flights` (persisted in the `flight_offers` table) can be
  booked — never a flight described in prose or found on the web.
- *Intent:* The `FlightOffer` table is the load-bearing control. Booking is
  irreversible and costs real money, so the design ensures JARVIS can only book
  something it actually retrieved and priced, gated behind confirmation *and* a
  TOTP code (chosen over SMS confirmation to avoid A2P dependency and SIM-swap
  risk). Trips are learned from email rather than airline logins so JARVIS holds
  no airline credentials.
- *Rejected alternative:* letting the agent book directly — rejected because it
  would put an irreversible financial action inside a sub-agent, outside the
  gate.

**4. Capabilities.**
- `search_flights` — real offer search; returns `offer_id`s.
- `list_trips` — booked trips from confirmation emails.
- (orchestrator) `book_flight` — gated + TOTP; books a retrieved offer.

**5. What it can't do.**
- **The agent cannot book** — *deliberate*. Booking is orchestrator-level.
- **Cannot book a flight it didn't retrieve** — *deliberate*. Only
  `flight_offers` rows are bookable.
- **Cannot access airline accounts** — *deliberate*. No airline credentials held.
- **Live booking is gated behind `BOOKING_ENABLED`** — currently a rollout gate
  pending Duffel live-mode activation.

**6. Dependencies.** `DUFFEL_API_KEY` (+ live key for real booking); `TOTP_SECRET`,
`OWNER_DOB`, `OWNER_GENDER` for booking; `flight_offers` table; `duffel`
component in the health model.

**7. Failure modes & remediation.**
- `401` → Duffel rejected the key. **Runbook:** check `DUFFEL_API_KEY`; if
  live-mode, confirm activation and prepaid balance.
- `booking_disabled` → `BOOKING_ENABLED=false`. **Runbook:** expected until
  live-mode rollout; flip when ready.
- `offer_expired` → the retrieved offer aged out. **Runbook:** re-search; offers
  are time-limited by the airline.

**8. UAT checks.**
- [ ] Search a known route → returns offers with `offer_id`s.
- [ ] Ask to book → routed through the gate + TOTP readback, not booked directly.
- [ ] Attempt to "book" a flight described in prose → refused (not in
  `flight_offers`).
- [ ] `list_trips` → shows trips parsed from confirmation emails.
- [ ] With `BOOKING_ENABLED=false` → booking pre-gate refuses cleanly.

**9. TDD reference.** `TDD-flight-booking.md`; `jarvis-flight-booking-status.md`;
the TOTP-before-offer bug docs.

---

### 7–13. Remaining agent chapters  *(STUBBED — fill from code + TDDs)*

- **7. `researcher`** — web search + page fetch (Tavily). Untrusted-content
  handling. Can't: save what it reads as durable facts.
- **8. `secretary`** — email drafting, tasks, ideas, contacts, Google
  Docs/Sheets, callbacks, watches. Can't: send email directly (gated); attach
  files to email (not built — needs Drive scope).
- **9. `navigator`** — traffic, find-place, current location. Depends on Tasker
  location push. Can't: make reservations.
- **10. `finance`** — stock quotes, portfolio (Alpaca read-only). Can't: place
  trades directly (gated).
- **11. `archivist`** — durable memory: remember/recall/forget/audit. The
  authoritative-block rule.
- **12. `infra`** — Fly.io hosted-app health + spend. Note the `$0 credit`
  over-warning to soften.
- **13. `netstatus`** — Proxmox / Uptime Kuma / Tailscale (local network).

### 14–17. Cross-cutting subsystems  *(STUBBED)*

- **14. The scheduler & morning brief** — APScheduler cron, heartbeat, missed-run
  catch-up. Governed by `TDD-jarvis-self-health-loop.md` §6.
- **15. The self-health loop** — checks, remediations, surfacing. Governed by
  `TDD-jarvis-self-health-loop.md`.
- **16. Runtime settings & the Admin page** — the settings overlay, the status
  view. Governed by `TDD-jarvis-self-health-loop.md` §7–8.
- **17. Auth & secrets** — the two Google auth systems, TOTP, whitelists, the
  full secret inventory and what each is for.

---

## Part III — Operations

### 18. Deployment
`fly deploy`, the three machines (ingest / worker / api), what each process does.

### 19. Configuration reference
Every Fly secret, its type, default, and effect. (The full table — this is the
reference that would have prevented this week's "which switch controls the
call" confusion.)

### 20. Repair runbooks
The consolidated remediation catalogue (mirrors the `remediation` table). One
place to look when something's red.

### 21. Testing & UAT
How to run the suite; how to do a clean per-component UAT pass using the Part II
chapters; reference to `docs/jarvis-test-plan.md`.

---

## Part IV — Product roadmap

Maintainable by either Matt or Claude. Add/updte rows as priorities shift.

| ID | Item | Component | Status | Priority | Notes |
|----|------|-----------|--------|----------|-------|
| R1 | Minute-granularity quiet hours + briefing exemption | scheduler/voice | In progress (PR-1) | High | Unblocks real 21:00–03:30 window |
| R2 | Runtime settings overlay + `get_effective` | settings | Planned (PR-2) | High | UI writes need runtime reads |
| R3 | Scheduler hardening (heartbeat, catch-up) | scheduler | Planned (PR-3) | High | Stops silent scheduler death |
| R4 | Relational health model (component/remediation/health_result) | self-health | Planned (PR-4) | High | Foundation for the loop |
| R5 | Health checks (liveness, secret-age, freshness, heartbeat) | self-health | Planned (PR-5) | High | Reads actions_audit as evidence |
| R6 | Admin read-only status page (topology + runbooks + evidence) | admin/ui | Planned (PR-6) | High | The always-available fallback |
| R7 | Morning brief health section (exception-only) | brief | Planned (PR-7) | Medium | Surfaces degraded/expiring |
| R8 | self_whoami + provenance + request log | self-health | Planned (PR-8) | Medium | "what am I, what have I done" |
| R9 | Google OAuth re-consent (Docs/Sheets scopes) | google_oauth | Blocked-on-owner | High | `python -m app.google_oauth` |
| R10 | Calendar service-account scope fix | scheduling | Blocked-on-owner | High | Re-share calendar / check scope |
| R11 | Consolidate calendar onto OAuth (one Google auth) | scheduling | Idea | Medium | Removes "half of Google works" |
| R12 | Twilio A2P re-registration under EIN (business framing) | twilio | In progress (owner) | Medium | Unblocks SMS; voice unaffected |
| R13 | Tasker durable fix + version-controlled export | navigator/location | In progress (owner) | High | Signal source for freshness check |
| R14 | Attach/share doc to email (needs Drive scope) | secretary | Idea | Low | New capability, own TDD |
| R15 | Duffel live-mode activation + `BOOKING_ENABLED=true` | travel | Blocked-on-owner | Medium | Real booking rollout |
| R16 | Soften infra `$0 credit` over-warning | infra | Idea | Low | Stops crying wolf on prepaid $0 |
| R17 | Multi-repo provenance ("head watching limbs") | self-health | Parked | Low | Needs inventory + access decision |
| R18 | User Guide as a web-app page | ui/docs | Planned | Medium | End-user facing; follows this guide |
| R19 | Push alerting (proactive degraded notifications) | self-health | Deferred | Low | After false-positive rate known |
| R20 | 4 AM wake-up-call crash — root-cause ticket | scheduler | Open bug | Medium | Request log makes it debuggable |

**Status legend:** In progress · Planned · Blocked-on-owner · Idea · Parked ·
Deferred · Open bug · Done.

---

## Appendix A — Secret inventory
(Full table of every Fly secret and its purpose — to be filled from
`fly secrets list` + config.py.)

## Appendix B — Fault-code catalogue
(Every fault code, its component, and its runbook — mirrors the `remediation`
table so guide and DB stay in sync.)
