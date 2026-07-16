# TDD — Episodic memory (durable, dated, cross-channel conversation memory)

**Author:** JARVIS design session (Claude Project)
**For implementation by:** Claude Code (CLI, against live repo)
**Status:** Draft for review. Load-bearing subsystem — health-loop scale, not a note.
**Prereqs:** Existing memory subsystem (`memory.py`, `Memory` table, `vectorstore`);
outbound-voice-party fix merged (so voice calls actually produce turns to distill).
**Date:** 2026-07-16

---

## 0. The ambition (one paragraph)

JARVIS is meant to be built over a decade. That only works if she *remembers* —
not the last 20 messages, but the shape of a relationship over years. The target
capability, stated as the owner did: *"Remember when we talked about that wearable
language-translation device a couple of years ago?"* must return a real answer.
Today it cannot: conversation turns live transiently (`messages` for text,
`voice_turns` for calls), nothing distills them into durable dated records, and
outbound-call turns never even reach `messages`. This TDD builds the **episodic
tier** of memory: at the close of any conversation, a distillation step produces a
dated, topic-tagged, embedded **Episode** — a summary plus verbatim quotes for
decisions/commitments — that persists indefinitely and is retrievable by meaning,
date, and topic. Raw transcripts are kept in cold storage for audit/replay but are
NOT the thing she remembers with.

**Why this is foundational, not a feature (the Tuckman lens).** A collaborator
moves through stages — forming, storming, norming, performing. JARVIS's *atomic*
memory (Tier 2: airport is SEA, boat is Serenity) is forming-stage knowledge: the
bare facts needed to function. But anticipating a need, inferring intent from
pattern, suggesting the thing that wasn't asked for — norming and performing — run
entirely on *non-atomic, accumulated, dated* context: what was discussed, what was
decided, how the owner's thinking on a topic evolved across months. Without an
episodic memory of the *relationship's history* (not just its facts), she is
structurally stuck at forming/storming — reactive, fact-retrieving, unable to
build on what came before. Episodic memory is the substrate that lets her progress
past that. It is the difference between a tool that answers and a chief of staff
who remembers where you left the wearable-translator idea and asks whether you
want to pick it back up. That capability is the point of building her over a
decade; this is the layer it rests on.

---

## 1. Where this sits — the three-tier memory model (naming what already exists)

JARVIS already has three memory tiers; only the third is missing. Naming them is
half the design, because the reconciliation rule between them is the crux.

| Tier | Table(s) today | Nature | Trust | Retrieval |
|---|---|---|---|---|
| **1. Authoritative** | `persona_profile`, `preferences`, owner ground-truth (`_owner_identity`) | Configured by the owner directly | HIGHEST — outranks everything | Always injected in preamble |
| **2. Atomic facts** | `memories` (`Memory`) | Inferred by the reflector from conversation; embedded | LOW ("may be wrong", said so in preamble) | pgvector similarity |
| **3. Episodes** ← NEW | `episodes` (+ `episode_quotes`) | Distilled dated *narrative* records of conversations | MEDIUM — summary is interpretation, quotes are verbatim | hybrid: embedding + date + topic |

**The reconciliation rule (THE key decision):**
- Tier 3 (episodes) is the *narrative* layer: "on DATE we discussed X." Dated,
  contextual, story-shaped.
- Tier 2 (facts) is the *atomic* layer: "home airport is SEA." Timeless, standalone.
- **Facts distill UP from episodes, not sideways.** A theme recurring across
  several episodes may graduate into a standing `Memory` fact (or, if stable
  enough, get promoted by the owner into a Tier-1 preference). Episodes are the
  raw ore; facts are refined metal. This keeps ONE fact store, not two competing
  ones — the trap this TDD exists to avoid.
- **Retrieval reaches Tier 3 and Tier 2 together.** When the owner asks
  "remember when...", the archivist searches episodes (narrative/dated) AND facts
  (atomic); the preamble already carries Tier 1. One retrieval surface, three
  sources, clear precedence (authoritative > episode-quote > episode-summary >
  inferred fact).

> Provenance discipline, turned inward. The preamble already warns that Tier-2
> facts "may be wrong." Episodes need the same honesty, but finer: a **summary**
> is JARVIS's interpretation (fallible), a **quote** is verbatim (load-bearing).
> "You decided X" must be quote-anchored; "we discussed X" may be summary. This is
> the `UNTRUSTED WEB CONTENT` fencing instinct from TDD #13, applied to her own
> recollection.

---

## 2. Decisions locked (owner, 2026-07-16)

1. **Persist episodes for recall; keep raw transcripts in cold storage.** Hot path
   = episodes (small, searchable). Cold path = raw turns (audit/replay). No
   double-write: the two layers hold *different things*. `voice_turns` is (or
   feeds) the cold store for voice; `messages` for text.
2. **Faithfulness: summary + verbatim quotes for key moments.** The summary
   carries gist; decisions and commitments are anchored to exact quotes. A
   misremembered *decision* is the expensive error — quotes fence it off.
3. **Design unified, build voice first.** Schema and pipeline are channel-agnostic
   from day one (voice, SMS, email, web all deposit the same Episode shape).
   First implementation targets voice, where the gap is live and the payoff
   vivid. Other channels slot in later with NO migration.

---

## 3. Data model

### `episodes`
```
id             pk
channel        str(32)          # voice|sms|email|web — the source
thread_key     str(255) index   # ties back to Conversation/OutboundCall/call_sid
occurred_on    date  index      # THE temporal handle ("a couple years ago")
occurred_at    datetime tz      # precise start, for ordering within a day
title          str(512)         # one-line human handle: "Wearable translator idea"
summary        text             # JARVIS's distilled narrative (interpretation)
topics         str/json         # tags: ["hardware","translation","product-idea"]
action_items   text/json        # extracted, may link to tasks.id
salience       float default 0.5# how much this mattered (drives retention/ranking)
embedding      text default ""  # JSON floats — SAME portable pattern as Memory
source_ref     str(128)         # e.g. call_sid or conversation_id for cold-store lookup
created_at     datetime tz
```

### `episode_quotes` (verbatim anchors — the faithfulness guarantee)
```
id            pk
episode_id    fk -> episodes.id (cascade)
speaker       str(16)          # owner|jarvis
quote         text             # VERBATIM — never paraphrased
kind          str(16)          # decision|commitment|key_fact|preference
turn_ref      str(64)          # voice_turns.id / message.id for provenance
```

**Why a separate quotes table:** decisions/commitments are the load-bearing,
must-be-exact fragments. Splitting them out (a) lets retrieval rank quote-anchored
claims above summary claims, (b) makes "show me exactly what I said" a clean query,
(c) keeps the summary honest — it can reference quotes rather than absorb-and-drift
them.

### Cold storage — reuse what exists, don't build new

**The design fork this resolves (read this before implementing).** At call end the
distiller must answer two questions: *where do I READ the raw conversation from,*
and *where does durable memory get WRITTEN?* There were two candidate shapes, and
choosing wrong here is expensive to unwind later:

- **REJECTED — the "mirror into `messages`" shape.** Copy every voice turn into the
  `messages` table (the same one text conversations use), so all conversations live
  in one unified store and memory reads from there. It *sounds* tidy — one
  conversation table — but it forces a **double-write**: every voice turn is
  written twice, once to `voice_turns` (which the live-call machinery requires) and
  once to `messages` (for memory), two copies that must stay in sync on a
  latency-sensitive path, for no actual gain. Worse, it corrupts the faithfulness
  model: a copied-and-reformatted turn in `messages` is no longer the pristine
  original, so validating a quote against it drifts.

- **CHOSEN — leave raw stores untouched, layer episodes on top.** `voice_turns`
  already persists every spoken turn (call_sid, turn, user_text, reply) — that IS
  the voice cold store, as-is. `messages` already persists every text turn — that
  IS the text cold store, as-is. Add nothing to either; don't delete from either
  (retention §7). The distiller READS from the appropriate raw store and WRITES one
  distilled Episode to the new `episodes` layer. Nothing is copied sideways.

So: the distiller's **input** is `voice_turns` (or `messages`); its **output** is
`episodes`. The raw tables stay pristine, which is precisely what makes the
verbatim-quote guarantee (§4 step 5) trustworthy — quotes are validated against an
original that was never reformatted. Test #16 enforces this by asserting
distillation never writes to `messages`; it guards against a future "helpful"
re-introduction of the mirror.

- Episodes carry `source_ref` so "replay the actual call" resolves back to the raw
  rows in whichever cold store produced them.

No new raw-transcript table. The cold store already exists per channel; the new
thing is only the distilled `episodes` layer on top.

---

## 4. The distillation pipeline

### Trigger — one channel-agnostic "close the episode" boundary
Each channel invokes the SAME `close_episode(db, channel, thread_key, source_ref)`
at its natural end:
- **Voice:** on call end (`/voice/status` completed, or the outbound_calls row
  going `done`). This is the first implementation.
- **Text (later):** on thread lull (no message for N minutes) or explicit
  "remember this" — a scheduler sweep, not inline.

Distillation is a JOB, never inline (same rule as task-push): it makes an LLM call
to summarize, which must not block a hangup or a reply. Enqueue on close; the job
worker distills out-of-band.

### The distillation step (`distill_episode` job)
```
1. Load raw turns for (channel, thread_key/source_ref) from the cold store
   (voice_turns or messages).
2. If < MIN_TURNS (e.g. 2 real exchanges) → skip. A one-line "you have mail"
   briefing that got hung up on is not an episode. (Guards against episode spam.)
3. LLM call → structured JSON:
     { title, summary, topics[], action_items[],
       quotes: [{speaker, quote, kind}], salience }
   Prompt demands quotes be VERBATIM copies of turn text (validated in step 5).
4. Embed the summary (+ title + topics) via vectorstore — SAME path as Memory.
5. VALIDATE quotes against source: each quote string MUST be a substring of some
   raw turn (speaker-matched). Non-matching "quotes" are DROPPED, not stored —
   a fabricated quote is the one unacceptable failure (it launders a hallucination
   into "your exact words"). Log drops loudly.
6. Persist episode + surviving quotes. Link action_items to tasks if any were
   created during the call (task.source/thread_key correlation).
```

### Fact graduation (Tier 3 → Tier 2), deliberately conservative
A separate, LATER sweep (not in the voice MVP): when a topic/claim recurs across
≥ N episodes with consistent content, propose a `Memory` fact via the existing
`remember()`. Owner-visible, reversible. NOT automatic in phase 1 — we watch what
episodes look like first before wiring auto-graduation, to avoid poisoning the
fact store with distillation errors.

---

## 5. Retrieval surface

One archivist entry point, hybrid query (pgvector + SQL), because "a couple of
years ago" is temporal, "wearable translator" is semantic, and "product ideas" is
topical — and Postgres/pgvector does all three in one place.

New/extended archivist tools:
- `recall_episodes(query, since?, until?, topic?)` — hybrid search. Embedding
  similarity over episodes, optionally filtered by date range and/or topic.
  Returns dated titles + summaries + linked quotes, ranked by (similarity ×
  salience), most-relevant first.
- `recall(query)` (the unified one) — searches episodes AND `memories` facts,
  merges, applies precedence (Tier1 preamble > quote > summary > fact). This is
  what "remember when we..." routes to. Extends the existing `_relevant_facts`
  rather than replacing it.

Preamble stays lean: episodes are NOT dumped into every request's preamble (that
would blow the context budget over years). They're RETRIEVED on demand when a
query implicates the past. Only Tier 1 is always-on; Tiers 2 and 3 are
query-triggered.

---

## 6. Voice-first implementation scope (the actual MVP)

What ships in phase 1:
- `episodes` + `episode_quotes` tables + Alembic migration (Postgres dialect
  guards per the `0001` convention; never `create_all`-only).
- `close_episode()` called on voice call end (in/outbound).
- `distill_episode` job (LLM distill + quote validation + embed + persist).
- `recall_episodes` / unified `recall` archivist tools, routed (description
  advertises "remembering PAST CONVERSATIONS by topic and date" — routing signal,
  or it's invisible, per the Docs bug).
- Voice allowlist: add `recall_episodes` (read-only, safe) to `VOICE_TOOLS_PHASE1`.

What's explicitly deferred (designed-for, not built):
- Text/SMS/email `close_episode` triggers (schema already supports them).
- Fact graduation sweep (Tier 3 → Tier 2).
- Episode curation UI (merge/correct/prune) beyond the tool-level forget.

---

## 7. Retention, forgetting, and curation

Memory you can't correct is worse than none (the archivist already has
`forget_fact`/`audit_memory`; episodes need the parallel):
- **Episodes are durable by default** — the whole point is decade-scale. No TTL on
  episodes.
- **Raw cold store (voice_turns/messages) MAY age** — e.g. keep verbatim turns hot
  for M months, then archive/compress. Episodes + their validated quotes survive;
  the full transcript is the expendable part. (Exact M is an owner call; default:
  keep everything until volume forces a decision — storage is cheap, regret isn't.)
- **Correction:** `forget_episode(id)` and `correct_episode(id, ...)` — merge
  duplicates ("the translator came up in 4 calls" → one project-memory or four?
  owner/archivist decides), fix a bad summary, drop a mis-scoped episode.
- **Salience-driven ranking, not deletion:** low-salience episodes rank lower;
  they're not purged. A decade of "confirmed, thanks" briefings shouldn't bury the
  translator idea, but nor should they be destroyed.

---

## 8. Test table

| # | Area | Test | Expected |
|---|------|------|----------|
| 1 | model | episodes + episode_quotes migrate (pg dialect guard) | tables exist, FK cascade |
| 2 | trigger | voice call end enqueues distill job | job row created, not inline |
| 3 | trigger | call with < MIN_TURNS | NO episode (spam guard) |
| 4 | distill | multi-turn call produces an episode | title/summary/topics/embedding present |
| 5 | quotes | decision stated on call → quote row, kind=decision | verbatim, speaker-matched |
| 6 | **quote validation** | LLM emits a quote NOT in any turn | DROPPED, logged — never stored |
| 7 | embed | episode embedding stored | vectorstore path, cosine fallback works in dev |
| 8 | recall | recall_episodes("wearable translator") months later | returns the dated episode |
| 9 | recall temporal | recall with since/until date range | filters correctly |
| 10 | recall unified | recall() merges episodes + facts, precedence right | quote-anchored ranks above inferred fact |
| 11 | precedence | Tier-1 ground truth vs contradicting episode | Tier-1 wins, per existing preamble rule |
| 12 | routing | archivist description advertises past-conversation recall | routing canary passes |
| 13 | voice | recall_episodes in VOICE_TOOLS_PHASE1 | reachable on a call |
| 14 | forget | forget_episode removes it from recall | gone from results |
| 15 | cross-channel | close_episode(channel="sms",...) works (design check) | episode created — proves unified shape |
| 16 | no-double-write | distillation does NOT copy turns into messages | messages unchanged; episodes populated |

---

## 9. What this is NOT

- **NOT mirroring voice turns into `messages`.** That was the plumbing framing we
  rejected. Turns stay in their per-channel cold store; episodes are the new,
  separate, distilled layer. (Test #16 enforces this.)
- **NOT dumping episodes into every preamble.** Retrieval is on-demand; only Tier 1
  is always-on. Decade-scale memory can't ride in the context window.
- **NOT auto-graduating facts in phase 1.** Distillation errors must not silently
  become "known facts." Graduation is a later, owner-visible sweep.
- **NOT storing paraphrased "quotes."** A quote is verbatim or it is dropped. The
  one unacceptable failure is laundering a hallucination into "your exact words."
- **NOT a second fact store.** Episodes and `memories` reconcile into ONE model
  (§1): narrative tier feeds atomic tier; retrieval unifies them.

---

## 10. Sequencing & open decisions

**Sequencing:** ships after the outbound-voice-party fix (needs real voice turns to
distill). Phase-1 = the four-layer pattern once more: tables+migration (exists),
job+trigger (distill), tools+routing (recall), voice allowlist. Then watch real
episodes accumulate before building graduation/curation.

**Open decisions for the owner:**
1. **MIN_TURNS threshold** — how substantive before a call becomes an episode? (2
   exchanges? Or salience-based — distill everything, let salience sort it?)
2. **Cold-store retention M** — keep verbatim turns forever, or age them after N
   months once the episode is distilled? (Default: keep, revisit on volume.)
3. **Salience source** — LLM self-assessed at distill time, or derived (call
   length, action items created, owner emotion)? Start LLM-assessed, refine later.
4. **Duplicate-topic policy** — the translator across 4 calls: one evolving
   project-memory or four episodes linked by topic? (Leaning: four episodes, one
   topic tag, and recall groups by topic — preserves the timeline, which IS the
   value of "a couple years ago.")

---

## Implementation note (phase 1, 2026-07-16)

Action-item→task linking (§4 step 6) was not wired: `Task` has no `thread_key`, so there is no correlation key between a call and tasks created during it — action items are stored as strings on the episode. Follow-up: add `Task.thread_key` (or equivalent) if linked episodes-to-tasks becomes worth having.
