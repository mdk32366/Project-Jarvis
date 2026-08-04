> ═══════════════════════════════════════════════════════════════════════════
> ⛔ GATED SPECIFICATION — NOT AN ORDER — DO NOT ACTION
> ═══════════════════════════════════════════════════════════════════════════
> **This is a design specification, not buildable work.** It describes a future
> target so the design is captured now; it does NOT authorize any implementation.
>
> **Gate:** DOUBLE-GATED: (1) D-075 survives AND (2) a census actually exists. Both required before any tranche/census UI is built.
> Until that gate opens, nothing in this document may be built, and no part of it
> should be treated as an order. A future Code session finding this file in `docs/`
> must NOT act on it — it is reference material, sequenced behind the gate above.
>
> **If you (Code) are reading this as a task: STOP.** This file being in `docs/`
> means the design is recorded, not that it is ready. Confirm with the owner before
> treating any GATED spec as work.
> ═══════════════════════════════════════════════════════════════════════════

# SPEC — the tranche / census UI (future, GATED — not orders)

> **Status: specification, not orders. DOUBLE-GATED.** This UI does not get built until BOTH:
> (1) **D-075 survives** (the structural axis is proven confidence-independent — otherwise the UI
> presents a confound as a prioritization axis), AND (2) **a census actually exists** (Phase 2
> assembly has produced surfaceome targets in disease tranches — otherwise there is nothing to
> display). Until both hold, this is a design target, not a build. Written 2026-08-01.
>
> **Why it cannot be ordered now:** ordering tranche navigation today means building presentation for
> data that does not exist, against a schema guessed in advance, showcasing an axis the pending run
> might invalidate. The first real census would reshape the schema; a collapsed D-075 would invalidate
> the axis. Both make the build wasted or misleading. The honest precursor — the F-009 cohort note —
> is orderable now (`ORDERS-Code-2026-08-01-F009-cohort-ui-note.md`); this is its eventual successor.

---

## §1 — What this UI eventually presents

When the census exists and the axis is proven, the app shifts from "a ranking over 82 comparator
targets" to "a navigable decision surface over the surfaceome census, organized by disease tranche."
The F-009 cohort note is the seed of this — it already tells the user "82 is a slice"; this UI shows
the whole.

**The core object is a decision surface with three ORTHOGONAL axes presented side by side, never
collapsed into one number** (the §3.3 discipline from the census spec, enforced in the UI):
1. **Structural suitability** — the (D-075-validated) structural score.
2. **Clinical burden** — the disease's prevalence × lethality.
3. **Delivery feasibility** — accessible vs. delivery-constrained (BBB/CNS flag).

**The UI must make the orthogonality visible, not hide it.** A target that is structurally excellent
but delivery-constrained (glioblastoma antigens behind the BBB) must read as *"suitable, but
delivery-limited"* — a category, not a demotion. Collapsing the three into a single rank would
reintroduce exactly the contamination the scoring discipline forbids.

---

## §2 — Tranche navigation

- **Primary organization: by disease tranche**, ordered lethality×prevalence → unmet-need (census spec
  §3.2). Tranche 1 (high-burden: lung, colorectal, pancreatic), Tranche 2 (unmet need: glioblastoma,
  ovarian; CNS/BBB as a flagged sub-tranche), then descending burden.
- **Each tranche is independently viewable and independently complete** — the UI must not imply the
  census is finished when only some tranches are folded. A tranche not yet folded shows as
  *"not yet assembled,"* not as empty or absent (the same honesty as the four-exit fold cascade: a
  gap is stated, never silent).
- **The four-exit partition surfaces per tranche** (unfoldable / below-floor / held-out-method / in
  ranking) — the held-out-logic doc's framing, already built for the 82, scales here. Every target's
  status is inspectable with its reason.

---

## §3 — ⚠ Honesty requirements that scale into the UI

These are not features; they are the conditions under which this UI is allowed to exist.

1. **The confound disclosure travels with the structural axis.** Whatever D-075 concludes about the
   pLDDT-attention confound must be visible wherever the structural score is shown — at census scale
   the confound is *worse* (census spec §4.1), so the disclosure is more important, not less. If
   D-075 only partially cleared it, the UI says so.
2. **Census completeness is stated, never implied.** "N of ~2,886 surfaceome targets assembled, in K
   of M disease tranches" — the denominator is always visible. The F-009 lesson at scale: never let
   the displayed set read as the universe.
3. **Environment/provenance per fold** (F-007). At census scale with possibly-institutional compute
   (census spec §6.1), folds may come from different environments. The UI should expose, or at least
   not obscure, that a target's features are comparable only within its boundary method — the
   commensurability seam must not be invisible.
4. **Differential-expression cutoffs are shown as owner choices, not facts.** The tumor-selectivity
   threshold that admits a target to the census is an owner-set modeling decision (census spec §4.3);
   the UI presents it as a parameter, not a ground truth.

---

## §4 — What it reuses (already built)

- The **four-exit fold-coverage partition** and the held-out-logic framing — scales directly.
- The **F-009 cohort note** — becomes the census's "what this is / isn't" explainer, expanded.
- The **held-out validation set** — if Phase B ran, the UI can show which census targets are
  clinically-validated positives vs. predictions.
- The **derive-don't-inscribe discipline** — every displayed number from an API/derived source, never
  a hardcoded literal (the two-sources-for-one-fact guard, at scale).

---

## §5 — Sequencing (when this unlocks)

```
D-075 run
  ├─ COLLAPSES → this UI is NOT built. The axis is not a valid prioritization signal.
  │              (App stays at: F-009 cohort note + Branch-B confound disclosure.)
  └─ SURVIVES → confound disclosure shipped (shaped by result)
                 → Phase 2 census assembly begins (census spec)
                    → Tranche 1 folded (needs compute; professor/institutional path)
                       → THIS UI becomes orderable, tranche by tranche
                          → each tranche displayed as it is assembled, completeness always stated
```

**This UI is orderable only when the first real tranche exists AND D-075 has survived.** Before that,
it is this document — a target, so that when the gate opens the build has a design and the honesty
requirements are pre-specified, not retrofitted.

---

## §6 — The dependency, stated once

Everything here is downstream of a single number-producing run whose interpretation is already sealed.
The F-009 note is the honest thing the app can say *now*. This is the honest thing it can say *once the
axis is proven and the census is real* — and not one step before.
