> ═══════════════════════════════════════════════════════════════════════════
> ⛔ GATED SPECIFICATION — NOT AN ORDER — DO NOT ACTION
> ═══════════════════════════════════════════════════════════════════════════
> **This is a design specification, not buildable work.** It describes a future
> target so the design is captured now; it does NOT authorize any implementation.
>
> **Gate:** D-075 must SURVIVE. A census scored on a confounded axis measures research attention across the surfaceome — worse than not building it.
> Until that gate opens, nothing in this document may be built, and no part of it
> should be treated as an order. A future Code session finding this file in `docs/`
> must NOT act on it — it is reference material, sequenced behind the gate above.
>
> **If you (Code) are reading this as a task: STOP.** This file being in `docs/`
> means the design is recorded, not that it is ready. Confirm with the owner before
> treating any GATED spec as work.
> ═══════════════════════════════════════════════════════════════════════════

# SPEC — Phase 2 census: the surfaceome source stack, disease-tranche organization, and the resource path

> **Status: specification, not orders. GATED on D-075 surviving.** Nothing in Phase 2 executes until
> the geom_proxy run resolves the fork (roadmap Part II). This document exists so that *the moment*
> D-075 survives, the census has a concrete data foundation, a tranche plan, and a fundable resource
> ask ready — not so that any of it starts now. Written 2026-08-01 (paper phase), after the surfaceome
> source research and the novelty check.
>
> **The one-line gate:** the census is only meaningful — and only novel — if the structural axis is
> confidence-independent. If D-075 collapses, this entire spec pauses: a 2,886-protein census scored
> on a pLDDT-tracks-attention axis is measuring research attention across the whole surfaceome, which
> is worse than not building it. **Do not begin the census on hope that D-075 survives.**

---

## §1 — What the census actually is (reframing "the list of overexpressed proteins")

"Overexpressed proteins" is not a bounded list — nearly every protein is overexpressed somewhere. The
census target is a **conjunction**, and each clause has an authoritative, downloadable source:

**A viable ADC target antigen is:**
1. **cell-surface accessible** → the human surfaceome
2. **differentially expressed tumor vs. normal** → TCGA (tumor) ∩ GTEx (normal) selectivity
3. **ideally internalizing** → clinical/experimental annotation (weaker, curated)

The census universe is therefore **the human surfaceome filtered by tumor-selectivity** — a bounded
set of ~2,886, not an open-ended "overexpressed" list. The Kathad 82 is a small, expression-selected
*slice* of this universe (which is exactly what F-009 established: comparator, not census). The
CD30/CD33/CEACAM5/Trop-2 false negatives are proteins *in the surfaceome, out of Kathad's slice* — a
surfaceome census contains them automatically.

---

## §2 — The source stack (all public, all downloadable)

| Layer | Source | Size | Access | Role |
|---|---|---|---|---|
| **Surfaceome (predicted)** | SURFY / **SURFY2** (meta-ensemble, 95.5% acc.) | ~2,886 | PNAS Dataset S1; bioRxiv SURFY2 | The denominator |
| **Surfaceome (experimental)** | Cell Surface Protein Atlas (CSPA) | 1,492 | wlab.ethz.ch/CSPA (Excel) | High-confidence validation subset |
| **Packaged** | `steveneschrich/surfaceome` R package | — | GitHub | CSPA + SURFY as data frames (download, not curation) |
| **Tumor expression** | TCGA | 15+ tumor types | public | The "tumor" half of selectivity |
| **Normal baseline** | GTEx | all normal tissues | public | The "vs normal" half — and toxicity flagging |
| **Tissue localization** | Human Protein Atlas | — | public | IHC-level localization (Kathad already used) |
| **Selectivity method** | S-score (surfaceome × TCGA, 15 tumors) | — | published method | The template for tumor-differential ranking |
| **Prior ADC ranking** | Kathad 82 | 82 | held | Phase-1 comparator, now a subset |
| **Clinical label** | Held-out set (Phase A, 20) + FDA/CT sweep | 20+ | built this session | Independent validation label |
| **Spatial (frontier)** | Floyd et al. 2025 surface-antigen clusters | 6,000+ assoc. | bioRxiv preprint | Phase-2 novelty layer, optional |

**The "where do we get the rest of the list" answer, in one line:** SURFY2 ∩ CSPA (downloadable),
filtered by TCGA/GTEx differential expression (downloadable). It is a download-and-threshold problem,
not a hunt.

---

## §3 — Tranche organization: by disease, ordered lethality×prevalence → unmet-need

The census is not folded in one batch. It is tranched **by the cancers each antigen presents in**,
weighted by prevalence and lethality — so each tranche is an independently meaningful, publishable
unit, and partial completion is still a complete story.

### §3.1 — Why disease-tranching (not fold-difficulty tranching)
Chunking by sequence length or fold-difficulty is engineering convenience. Chunking by disease makes
each tranche a *clinical* unit: "surface antigens differentially expressed in cancer X" stands alone
whether or not the rest is ever folded. It front-loads clinical impact and makes the BBB/CNS
delivery-axis a natural tranche boundary rather than a bolt-on.

### §3.2 — The order (ruled by owner: 1 then 2)
1. **Tranche 1 — Lethality × prevalence.** Highest-burden cancers first: lung, colorectal, pancreatic,
   and the other top mortality×incidence malignancies. *Rationale:* establishes the platform where
   being right matters most and where TCGA/expression data is richest and best-validated. Tranche 1's
   paper: "the method works on well-characterized high-burden disease."
2. **Tranche 2 — Unmet need.** Cancers with few or no ADC options: glioblastoma, ovarian, and other
   low-option malignancies. *Rationale:* this is where a prioritization platform earns its keep —
   producing candidates where the field is starved. Tranche 2's paper: "the platform surfaces
   candidates where none existed." **Glioblastoma/CNS forms its own sub-tranche flagged
   'structurally suitable, delivery-constrained'** — the BBB axis is baked into the organization.
3. Later tranches: remaining differentially-expressed surfaceome, by descending burden.

### §3.3 — ⚠ The orthogonality discipline (the load-bearing rule)
**Lethality/prevalence orders which targets are folded first. It must NEVER enter the structural
score.** Structure answers *can an ADC reach this antigen*; disease-stacking answers *does this
antigen matter clinically*. The entire value is keeping these orthogonal:
- **Tranche *order* by lethality — yes** (what you fold first).
- **Tranche *score* by lethality — never** (contaminates the measurement, exactly as the
  attempt-history label must never touch the geometry).
The final decision surface presents the two axes *side by side* — structural suitability × clinical
burden × delivery feasibility — never collapsed into one contaminated number.

---

## §4 — ⚠ Three hazards that scale with the census (each already has machinery)

Going from 82 to ~2,886 makes three things load-bearing that were edge cases before:

1. **The pLDDT-attention confound gets WORSE.** The 82 were relatively well-studied. A full surfaceome
   includes hundreds of poorly-characterized proteins with few structural homologs → ESMFold
   confidence varies far more → correlates harder with research-attention. **This is the concrete
   reason Phase 2 is gated on D-075.** If the axis is confidence-dependent, the census amplifies the
   confound across the whole surfaceome. The census is only valid if D-075 shows confidence-independence.
2. **The fold-coverage cascade becomes the main event.** Three unfoldable/held-out of 82 is an edge
   case; across 2,886 large multi-pass and glycosylated surface proteins it is constant. **The
   four-exit partition (unfoldable / below-floor / held-out-method / LOO) already built for the 82 is
   the machinery** — it will run continuously, and the held-out-logic doc's framing scales directly.
3. **"Differential expression" is an owner-reserved modeling choice.** How much tumor-over-normal is
   "selective"? Which normal tissues are disqualifying (cardiac, neural = toxicity red flags
   regardless of tumor expression)? The S-score is a starting template; the cutoffs are Owner
   judgment, not an automated threshold — the same domain-decision reservation as label
   classification and scope trade-offs.

---

## §5 — ⚠ Novelty is CONDITIONAL, and the condition is D-075

An honest novelty check (this session's surfaceome research + the earlier Grok pass):

**Not novel on their own** — a reviewer would cite prior work for each:
- Surfaceome census → SURFY/CSPA.
- Surfaceome × TCGA differential expression → the S-score paper (this is *exactly* "surface proteins
  ranked by tumor selectivity across 15 cancers").
- Structure-based target features → PNAS surfaceome mapping, epitope tools.
- Disease-association prioritization → ImmunoTar, Kathad.

**Novel — the conjunction nobody has assembled:** predicted-ECD structural suitability *as a ranking
axis* + disease/prevalence/lethality stacking + delivery-axis (BBB) flagging, over a surfaceome
census, as a single decision surface for ADC prioritization. **This is platform/integration novelty,
not method novelty** — and it is real *if and only if the structural axis adds information*, i.e. if
D-075 survives. If D-075 collapses, the "novel stack" reduces to S-score + a confound + a disease
overlay, which is not a positive contribution.

**Two things this requires:**
- **State the novelty as integration, not method** — the same discipline as the Phase-1 deck (drop
  method-novelty language). Grok's "manufactured gap" critique applies with full force to the census
  layer if overclaimed.
- **The systematic literature check is still open** (Site4Drug, PNAS surfaceome, from the planning
  session). "I searched and found no assembled equivalent" is weaker than a systematic review's
  finding for a paper's novelty claim. Close it before the census novelty is asserted in print.

---

## §6 — The resource / compute path (flag: this section doubles as a professor-facing ask)

*Separated so it can be lifted for a resource request without contaminating the technical spec.*

**The bottleneck has been compute**, not method: local RTX 2000 (8 GB, ~440 aa ceiling), rental A6000
($0.54/hr) for large targets. Folding ~2,886 *ECDs* (mostly far smaller than full proteins, since
sliced to the ECD) is fast per-fold on ESMFold; the cost is the large-protein class (MUC16/FAT2 tier)
and aggregate throughput.

**If institutional GPU access is available** (the UCLA-contact path from D-072, or Stevens compute):
1. **The FAT2/MUC16-class "on ice" folds unlock** — their gate was *resource access*, not method
   (D-072 Tier 2/3). Institutional big-VRAM cards are exactly what they waited for.
2. **Census throughput becomes batch-scheduling, not per-hour budget** — tractable.
3. **The tranche plan IS the resource-request structure.** "Compute for Tranche 1 (top-lethality
   cancers, ~N targets)" is a concrete, fundable ask — far better than "fold the surfaceome." Each
   tranche is a bounded, justifiable unit.

### §6.1 — ⚠ The commensurability guard on external/institutional folds
Free compute does not relax the comparability discipline — it stresses it. **Every fold produced on
other hardware must enter through the gate and name how it is known (F-007's lesson):**
- environment manifest captured (ESMFold version, quantization, chunk settings) per fold;
- the same pLDDT floor and boundary-method checks as the existing 79;
- folds from a drifted environment are **not comparable** to the sliced-ECD 79 — the same seam as
  the stitched-FAT2 problem, at scale. More hands + more hardware = more ways for the environment to
  drift silently.
A census with invisible environment seams is worse than a smaller consistent one.

---

## §7 — Sequencing (what unlocks when)

```
D-075 run  ──SURVIVES──>  structural axis is real and confidence-independent
                          │
                          ├─ census is meaningful AND novel (as integration)
                          ├─ download SURFY2 ∩ CSPA, filter by TCGA/GTEx  [data assembly]
                          ├─ owner sets differential-expression cutoffs   [owner-reserved]
                          ├─ Tranche 1 (lethality×prevalence) folds        [needs compute]
                          │     └─ professor/institutional compute = the enabler
                          ├─ Tranche 2 (unmet need, incl. CNS/BBB sub-tranche)
                          └─ each fold through the gate, environment captured
           ──COLLAPSES──> census PAUSES. Axis is not confidence-independent.
                          The S-score + confound + overlay is not a contribution.
                          Pivot to Branch B (cautionary methods paper).
```

**Nothing in §2–§6 begins before D-075 resolves.** The spec's value now is that it *exists* — a
concrete data foundation, a tranche plan, a fundable ask, and the hazards named — so the moment the
fork resolves in favor of survival, Phase 2 has a running start and the professor can be handed a
real resource request. Not a reason to fold before the run.

---

## §8 — Carried dependencies

- **Gated on:** D-075 survival (the run, not yet authorised).
- **Requires (owner):** differential-expression cutoffs; disqualifying-normal-tissue list; tranche
  disease assignments.
- **Requires (still open):** the systematic literature check (§5) before census novelty is asserted.
- **Enabled by:** institutional compute (§6) — turns Phase 2 from budget-constrained to
  schedule-constrained, and unlocks the D-072 Tier 2/3 large folds.
- **Reuses (already built):** the four-exit fold-coverage partition; the held-out validation set; the
  gate + environment-capture discipline (F-007).
