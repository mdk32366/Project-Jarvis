# TDD — Backlog Item #14: Authenticated App UI — Narrative Landing & Comprehensive Admin

**Status:** Needs a few scoping calls before build (see §7)
**Repo:** `mdk32366/Project-Jarvis` (Vite frontend, FastAPI backend, `jarvis-mdk.fly.dev`)
**Prereq:** TDD #12 (self-whoami, at least Phase 1+2) should exist before the
admin dashboard can show real data instead of placeholders. The landing page
(§2) has no such dependency and can ship first.

---

## 0. What exists today, and why it looks the way it does

`jarvis-mdk.fly.dev/` is **not a product page** — it's the A2P 10DLC carrier
compliance page (opt-in disclosure, sample messages, consent flow) that got
the Twilio texting campaign approved. **This page's content is
load-bearing for carrier compliance and must not be rewritten or
restructured** as part of this work. If Twilio or a carrier re-reviews the
campaign, this is the page they land on.

Past `/login`, the app is a Vite SPA — the real authenticated product. Two
things are true about it right now:

1. The browser tab still reads **"Vite + FastAPI Starter"** — literally the
   scaffold default, never touched. `/login` itself renders as an empty
   shell to an unauthenticated fetch (correct — it's client-rendered), but
   the fact that the `<title>` was never changed suggests the app shell in
   general hasn't had a pass since scaffolding.
2. There's an admin tab intended to show attached APIs / status, but per
   Matt: it doesn't currently tell a coherent story about what's actually
   attached, healthy, or happening.

**This TDD is scoped entirely to the authenticated app** (`/login` and
everything behind it). It does not touch `/`, `/terms`, `/privacy`, or
any other public compliance-adjacent route.

---

## 1. What we're building

Two things, both inside the authenticated shell:

1. **A narrative landing view** — the first thing you see after login,
   above the always-present prompt/chat UI. Answers "who is JARVIS, what
   can she do, why does this matter" — not a settings page, a front door.
2. **A comprehensive admin view** — a real, accurate accounting of every
   moving part: connected services and their health, the morning brief's
   actual contents, the self-whoami faculties (provenance, request history,
   credential health), and anything else genuinely operational. Replaces
   the current admin tab, which doesn't do this today.

Plus a trivial fix: the `<title>` and any other scaffold leftovers in the
app shell.

---

## 2. The landing view

### 2.1 Job of the page

One job: in the time it takes to glance at it, convey **who JARVIS is, what
she can actually do, and why that's worth something** — before the person
(Matt, or anyone he ever shows this to) drops into the prompt box and starts
typing. This is not a settings page and not the admin view (§3) — it's the
pitch, told honestly, using real capabilities, not aspirational ones.

The prompt/chat UI stays pinned at the top or otherwise persistently
available — this page doesn't replace the ability to just start talking to
her, it sits alongside it.

### 2.2 What it should say — drafted narrative, not placeholder copy

Real capabilities as of this TDD, grouped the way a person would actually
think about them rather than by internal architecture:

**She has faculties, not just tools.**
- **A sense of herself** — self-whoami: what code she's running, when she
  went into service, her own uptime and version history (TDD #12 Phase 1).
- **A sense of history** — a durable log of what she's been asked and how
  each request resolved, so "what happened with X" is a question she can
  actually answer instead of a shrug (TDD #12 Phase 2).
- **A sense of you** — the owner profile (whoami), preferences, and
  context she carries across conversations.
- **A sense of time** — she knows what time it actually is, in her own
  timezone and yours, not inferred from a stray email timestamp
  (TDD #11).
- **A sense of her own health** — she watches the services she depends on
  (Duffel, Twilio, Google, Tavily) and knows when something's degraded
  before it causes a failure Matt has to notice first (TDD #12 Phase 3).

**What she actually does, in plain terms** (pull from what's real and
shipped — do not list aspirational/unbuilt items here; keep those, if
shown at all, in a clearly-labeled "coming soon" section):
- Answers questions and handles requests over text and voice.
- Delivers a morning brief — weather, calendar, schedule, news, traffic —
  without being asked.
- Searches and books real flights, behind a confirmation gate and a second
  factor, spending real money only when explicitly told to (TDD flight
  booking — live once Duffel activation completes).
- Manages contacts and tasks via Google.
- Watches the homelab — Proxmox nodes, Uptime Kuma monitors — and reports
  status.
- Reads and drafts email; sends only with explicit confirmation.

**The ROI framing — say it in real terms, not marketing language.**
Matt's instinct that "there's a real ROI story" is right, but the honest
version of that story is **time saved on specific, recurring things** —
not a vague productivity claim. Concretely, once the request log (TDD #12
Phase 2) exists, this can eventually be **data-driven** ("47 requests
handled this month, avg response under Xs") rather than asserted copy.
Until that data exists, keep the ROI section qualitative and specific
("no more checking four apps for a morning brief") rather than inventing
numbers. **Do not fabricate a metric — if precise numbers aren't available
yet, say what she saves in kind (fewer app-switches, no manual flight
search, one text instead of five), not in a fake statistic.**

### 2.3 Design approach

This needs actual visual design work, not just copy — a distinctive,
intentional layout matching the subject (a personal AI assistant with real
operational depth, not a generic SaaS dashboard). That's a separate design
pass best done in a live build session against the real Vite app (so type
scale, color tokens, and layout can be iterated against the actual
component library already in use) rather than speced blind here. Bring the
`frontend-design` skill/discipline to that session: ground it in what
JARVIS specifically is, avoid template defaults, and treat the "faculties"
framing above as the structural device the layout can lean on (each
faculty as a distinct, well-labeled section rather than a generic feature
grid).

### 2.4 Tests / acceptance

| Check | Property |
|---|---|
| Landing view renders behind auth only | Not reachable without login — this is not a replacement for or addition to the public compliance page. |
| Prompt UI remains present and functional on this view | The narrative page augments, doesn't replace, the ability to just start typing. |
| No capability is listed that isn't actually shipped | Cross-check copy against what's actually deployed at write time; "coming soon" items are visually distinct from live ones. |
| No fabricated metrics in the ROI section | If Phase 2's request log isn't live yet, no invented numbers appear. |
| `<title>` reflects the product, not the scaffold | Trivial but real — "Vite + FastAPI Starter" replaced everywhere it appears (tab title, any meta tags). |

---

## 3. The admin view

### 3.1 Job of the page

A real, accurate, comprehensive accounting of JARVIS's pieces and parts —
the thing the current admin tab was supposed to be and isn't. This is
where "is everything actually working" gets answered at a glance, and
where the operational detail the landing page (§2) intentionally
simplifies gets shown in full.

### 3.2 Sections

Structured around what's genuinely inspectable, most of it now defined by
TDD #12 (self-whoami) — **this page is largely a UI on top of that TDD's
data, plus a few things that predate it:**

**Identity & provenance** (TDD #12 Phase 1)
- Current git commit, commit message, commit date
- Deployed-at, first-deployed-at, days in service
- Repo link

**Recent activity** (TDD #12 Phase 2 — request log)
- A queryable table/feed: recent requests, channel (voice/sms/scheduled),
  disposition (completed/error/gated_pending/gated_cancelled/refused),
  duration
- Filterable by disposition — this is the view that would have shown the
  wake-up-call failure immediately, as it happened, instead of only
  learning about it from Matt reporting "she hung up on me."

**Service health** (TDD #12 Phase 3)
- Per-service status: Duffel, Twilio, Google OAuth, Tavily, Anthropic,
  weather, Maps
- Last success / last failure timestamps
- **Secret age**, per credential, from Fly secrets metadata — surfaced
  here as a real "consider rotating" signal, not a fabricated countdown
  (per TDD #12 §5.1's corrected framing)
- True expiry where a service actually publishes one (OAuth)

**Morning brief composition** (new to this TDD — not previously specced)
- What actually goes into it today: **weather, calendar, schedule
  readback, news, traffic** (confirmed with Matt during this session).
  Show each source's own health status inline (a "degraded" traffic API
  should be visible here, not just buried in the generic service-health
  list) — this is the one place where "is the thing I get every morning
  actually going to work tomorrow" is answerable directly.
- **This needs its own short discovery pass against the actual
  `handlers/` code that assembles the brief** — the TDD list above is
  Matt's description of it, not a verified enumeration of every data
  source and every conditional branch (e.g., does the brief skip news on
  weekends? does it degrade gracefully if traffic data times out?). Don't
  guess at that logic; read the handler before building this section.

**Connected integrations** (what the current admin tab was trying to be)
- Duffel (test/live mode, `BOOKING_ENABLED` state)
- Twilio (SMS/voice, A2P status)
- Google OAuth (scopes granted — Contacts, Tasks, and eventually
  Docs/Sheets per TDD #13 if built)
- Tavily
- Google Maps
- Tailscale mesh — node count, Uptime Kuma summary

**Multi-repo / other hosted apps** (TDD #12 Phase 4, if and when built)
- Explicitly out of scope until Phase 4's access-model decision is made —
  don't build a placeholder section that implies data exists when it
  doesn't.

### 3.3 Design approach

Unlike the landing page, this is a **data-density-first** view — the
opposite design problem. Legible tables, clear status colors (a
consistent, small status vocabulary — ok/degraded/error/unknown — used
identically across every section rather than each section inventing its
own), and no decoration that competes with scanability. This is a page
Matt will actually use to debug something at 6am, not a page meant to
impress. Treat "quick to parse under stress" as the actual design
constraint.

### 3.4 Tests / acceptance

| Check | Property |
|---|---|
| Every section reflects live data, no hardcoded placeholders | Especially service health and secret age — this replaces a tab that was already not doing this job; don't ship a second version of the same problem. |
| Morning brief section matches actual handler logic | Verified against `handlers/` code, not asserted from memory of what it "should" do. |
| Status vocabulary is consistent across all sections | ok/degraded/error/unknown used the same way everywhere, not per-section ad hoc labels. |
| Request log is filterable and paginated | Matches TDD #12 §4.3's `get_recent_requests(n, disposition_filter)`. |
| Secret age never shown as a fabricated expiry | Same constraint as TDD #12 — age-since-rotation, not an invented countdown, for credentials without a real one. |
| Page degrades gracefully if a data source is unavailable | The admin page itself failing because one health check is slow/down would be a bad irony — each section should fail independently, not take the whole page down. |

---

## 4. The title/scaffold fix

Trivial, do it regardless of everything else in this TDD:

- `index.html` `<title>` → something real ("JARVIS" or similar)
- Check for other scaffold leftovers: favicon, meta description, any
  placeholder text still reading "Vite + FastAPI Starter" or similar
  default strings anywhere in the shell
- Five-minute fix, no reason to bundle it with the larger design work —
  can ship immediately, independently

---

## 5. Implementation notes

- This is a **frontend-heavy** TDD against the existing Vite app — unlike
  prior TDDs in this project, most of the new code is UI, not backend
  handlers. The backend surface it depends on is almost entirely already
  specified by TDD #12 (self-whoami) and existing handlers (morning brief).
- **Sequencing matters:** the admin view's most valuable sections (request
  log, service health, secret age) don't have real data to show until
  TDD #12 Phases 1–3 are built. Building the admin UI first means either
  placeholder data (bad — looks done, isn't) or blocking on backend work.
  **Recommend: land TDD #12 Phases 1–2 first, then build the admin view
  against real data, then Phase 3 health data fills in the remaining
  section.** The landing view (§2) has no such dependency and can be built
  independently, any time.
- Bring the actual Vite repo / component library into the build session —
  this TDD describes structure and content, not literal component code,
  since I don't have the current frontend source to build against
  directly in this session.

---

## 6. Things I would push back on, if asked

- **Don't touch `/` or anything compliance-adjacent.** It's tempting to
  see a generic-looking public page and want to "fix" it too, but that
  page's exact wording is why the A2P campaign got approved. Leave it
  alone; everything in this TDD is behind `/login`.
- **Don't invent ROI numbers.** "Time saved" is a real and fair claim once
  the request log has real data to point to. Until then, say what she
  does in concrete, checkable terms — not a percentage or hour count
  nobody can verify.
- **Don't build the admin page's data sections against guesses about what
  the morning brief does.** Read the actual handler. "Weather, calendar,
  schedule, news, traffic" is Matt's accurate high-level description, but
  the admin page needs to reflect the real conditional logic (what
  happens when a source fails, what's included on which days), not a
  restatement of the high-level list.
- **Don't build a placeholder Phase-4 (multi-repo) section.** An empty or
  fake "other apps" section on the admin page implies capability that
  doesn't exist yet and undercuts the whole point of this page being
  trustworthy.
- **Don't let the admin page inherit the landing page's design language
  wholesale.** They have opposite jobs — one persuades/orients, the other
  is a debugging tool used under time pressure. Sharing a design system
  (colors, type) is right; sharing a layout philosophy (dense vs.
  spacious) is wrong.

---

## 7. Decisions needed before build

1. **Landing page build timing** — ship now (no backend dependency) or
   bundle with the admin page for a single coherent design pass? Either
   is reasonable; recommend shipping the landing page first since it's
   unblocked and the title/scaffold fix can ride along with it.
2. **Admin page sequencing** — confirm the recommendation in §5: land
   self-whoami Phases 1–2 before building the admin UI's data sections,
   rather than building UI against placeholders now.
3. **Morning brief discovery pass** — who does this, and when? It's a
   prerequisite for an accurate §3.2 "morning brief composition" section
   and hasn't been done yet (this TDD describes the *page*, not the
   verified brief logic).
4. **Design session logistics** — this TDD recommends doing the actual
   visual design work in a live session against the real Vite app rather
   than blind. Confirm that's the plan, and whether that session brings
   the repo via upload (as with the Duffel build) or happens in Claude
   Code / another environment with direct repo access.

---

*Two pages with opposite jobs sharing one authenticated shell: one tells
JARVIS's story honestly (real faculties, real capabilities, no invented
numbers), the other is a debugging instrument (dense, accurate, fails
gracefully section-by-section). Both are mostly UI work sitting on top of
data TDD #12 already defines — the interesting sequencing question is
backend-before-frontend, not the design itself.*
