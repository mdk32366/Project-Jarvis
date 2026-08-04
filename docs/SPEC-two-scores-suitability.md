> ═══════════════════════════════════════════════════════════════════════════
> ⛔ GATED SPECIFICATION — NOT AN ORDER — DO NOT ACTION
> ═══════════════════════════════════════════════════════════════════════════
> **This is a design specification, not buildable work.** It describes a future
> target so the design is captured now; it does NOT authorize any implementation.
>
> **Gate:** D-075 must SURVIVE (geom_proxy run must show the structural axis is confidence-independent). Structural Suitability cannot ship if the axis is confounded.
> Until that gate opens, nothing in this document may be built, and no part of it
> should be treated as an order. A future Code session finding this file in `docs/`
> must NOT act on it — it is reference material, sequenced behind the gate above.
>
> **If you (Code) are reading this as a task: STOP.** This file being in `docs/`
> means the design is recorded, not that it is ready. Confirm with the owner before
> treating any GATED spec as work.
> ═══════════════════════════════════════════════════════════════════════════

# SPEC — the two orthogonal scores: Structural Suitability + Clinical Opportunity (GATED, not orders)

> **Status: specification, not orders. Structural Suitability is D-075-GATED; Clinical Opportunity is
> partially buildable but deliberately sequenced behind the confidence-demotion fix.** This captures
> the design so the scores land correctly when their gates open. Written 2026-08-01.
>
> **The load-bearing principle, stated once:** these are TWO orthogonal scores, shown side by side,
> **NEVER blended into one number.** Collapsing them is the D-028 sin (a real distinction destroyed)
> AND reintroduces the confound (market/opportunity correlates with how-studied a target is, which
> correlates with pLDDT/attention — the exact contamination D-075 isolates). A single "Suitability"
> score would be *more* confounded than confidence, not less. The orthogonality IS the contribution.

---

## §1 — Why two scores (the neophyte problem this solves)

On the list today, "Confidence" is the most prominent per-target signal, so a neophyte reads a green
confidence dot as "good target" — promoting a fold-quality metric into a suitability verdict it was
never meant to be (the confidence-demotion order fixes the *impersonation*; this spec provides the
*real* signals that belong in that slot).

The owner's example — *"why NECTIN4 (2 markets) when ADAM17 opens 10?"* — contains TWO different
questions that must not be merged:

| | Axis 1 — **Structural Suitability** | Axis 2 — **Clinical Opportunity** |
|---|---|---|
| **Answers** | *Can an ADC physically work against this antigen?* | *Is this antigen worth pursuing?* |
| **Built from** | ECD geometry, accessibility, epitope reachability (Meaning C) | tumor-type breadth, prevalence, lethality, unmet need |
| **Gated on** | **D-075** (this IS the axis the ablation validates) | partially available now (expression breadth exists); prevalence/lethality/unmet-need need new sources |
| **Confound risk** | the pLDDT-attention confound itself | expression-breadth correlates with attention — see §4 |

A target can score high on one and low on the other. **That is not a contradiction — it is the entire
point.** NECTIN4 structurally-excellent but narrow-indication; ADAM17 broad-indication but (say)
structurally-harder is *exactly* the tension a decision-maker needs to see, and it is invisible if the
two collapse into one rank.

---

## §2 — Axis 1: Structural Suitability (D-075-GATED)

**This is Meaning C — the geometry-not-confidence score.** It is the thing D-075's `geom_proxy`
ablation is validating. **It cannot ship until D-075 resolves**, because:
- if D-075 shows the geometric signal survives without pLDDT → Structural Suitability is real and
  shippable, built on the confidence-blind features;
- if D-075 shows the signal was mostly pLDDT/attention → there is no valid structural suitability axis
  to display, and shipping one would teach users a confounded score (the exact failure the run prevents).

**Design (contingent on survival):**
- Built from the geometry features the ablation validated (ECD length, Rg, SASA, largest-patch, and
  the confidence-blind membrane-proximal SASA proxy) — **NOT** from pLDDT-derived features, so the
  displayed suitability score is itself confound-resistant by construction.
- Presented with its own honesty: it is a *ranking* signal at n=12, not a clinical verdict; the
  same "detection not explanation" boundary the scorer already carries (MethodNote).
- Confidence sits BENEATH it as an input (does the fold mean anything → feeds → is the target
  structurally viable), never beside it as a peer. This is the hierarchy the confidence-demotion order
  reserves the slot for.

---

## §3 — Axis 2: Clinical Opportunity (partially available, sequenced)

**The ADAM17-opens-10-markets axis.** Some raw material ALREADY EXISTS and its honesty boundary is
already documented — but that same fact is a trap (§4).

**What exists now (`data/cancer_associations.csv`, D-053):**
- Per-target count of tumor types where the target is highly expressed (the paper's quasi-H-score ≥
  150). **This is the "10 markets" number** — OSMR = 10 indications is already computed.
- ⚠ **But the file states precisely what this is:** *"an EXPRESSION claim… NOT a causal claim, NOT a
  claim that the target drives the disease, and NOT a clinical indication."* The count is
  "tumor types with high expression," NOT "approved indications" and NOT "market size."

**What Clinical Opportunity needs beyond expression breadth (new sources, Phase-2-adjacent):**
- **Prevalence** — how common each cancer is (SEER / GLOBOCAN — public).
- **Lethality** — mortality/survival per cancer (SEER — public).
- **Unmet need** — existing therapy options per indication (curated).
- These are the disease-stacking layer from the census spec; they turn "expressed in 10 tumor types"
  into "opens N high-lethality, high-prevalence, low-option indications" — the real opportunity signal.

---

## §4 — ⚠ The confound trap in Axis 2 (why "10 markets" must be handled carefully)

**Expression breadth correlates with research attention**, the same pathway D-075 fights:
well-studied targets are studied *because* they're broadly expressed in cancer, so "expressed in many
tumor types" partly tracks "much-studied" tracks "high pLDDT." If Clinical Opportunity is built naively
from expression breadth alone, **it is not orthogonal to Structural Suitability — it shares the
attention confound**, and showing them "side by side" would be showing two views of the same bias.

**The discipline that keeps Axis 2 honest:**
1. **Prevalence and lethality are NOT attention-confounded** — a cancer's mortality rate is an
   epidemiological fact independent of how much a target is studied. Building Opportunity primarily on
   prevalence×lethality×unmet-need (not on expression breadth) makes it genuinely orthogonal to Axis 1.
2. **Expression breadth is the WEAKEST input to Opportunity, flagged as such** — it's the "10 markets"
   headline a neophyte wants, but it must be labeled as expression, not indication, and must not be the
   dominant term. The honest Opportunity score is "how much disease burden does this target address,"
   anchored on burden (prevalence×lethality), not on how many papers exist.
3. **Never claim indications the data doesn't support.** "Expressed in 10 tumor types" ≠ "10 markets"
   ≠ "10 approved indications." The UI copy must not let the neophyte's "10 markets" reading overstate
   what expression breadth means (the F-009 discipline: don't let a slice masquerade as the whole).

**The test of orthogonality:** if Structural Suitability and Clinical Opportunity turn out strongly
correlated across targets, that is a RED FLAG that Opportunity is smuggling in attention/expression
bias — not a confirmation that "good targets are good." Genuinely orthogonal axes should be roughly
uncorrelated. (Worth a pre-registered check when both exist.)

---

## §5 — The display (when both exist)

- **Two scores, side by side, never multiplied.** A target card / list row shows Structural Suitability
  and Clinical Opportunity as separate, separately-labeled signals with separate provenance.
- **The 2D view is the "duh" insight made visible:** a scatter (structural × clinical) where NECTIN4
  and ADAM17 land in different quadrants is *exactly* the "why NECTIN4 not ADAM17" question, shown not
  answered — the decision-maker sees the tradeoff and rules, the tool does not collapse it for them.
- **Confidence demoted beneath Structural Suitability** (the hierarchy from the demotion order):
  Confidence (is the fold real) → feeds → Structural Suitability (is the target viable) → beside →
  Clinical Opportunity (is it worth it).
- **Each score carries its own honesty boundary** — Structural: ranking-not-verdict, n=12; Clinical:
  burden-not-indication, expression≠market.

---

## §6 — Sequencing (what unlocks when)

```
NOW (un-gated):
  └─ Confidence demotion (ORDERS-...-confidence-demotion.md) — stop the impersonation, reserve the slot.

D-075 run:
  ├─ SURVIVES → Structural Suitability (Axis 1) becomes buildable on confidence-blind features.
  │             └─ then Clinical Opportunity (Axis 2) built on prevalence×lethality×unmet-need
  │                (§4 discipline), expression-breadth as flagged-weakest input.
  │                └─ then the two-score side-by-side display (§5), orthogonality checked (§4 test).
  └─ COLLAPSES → NO Structural Suitability score ships (no valid axis). Clinical Opportunity could
                 still exist alone, but "suitability" as a structural claim is off the table — and the
                 confidence demotion (already shipped) correctly leaves the target-quality slot empty.
```

**Nothing in §2/§5 ships before D-075. Clinical Opportunity (§3) can begin curation (prevalence/
lethality sources) independent of the run, but its display beside Structural Suitability waits, because
a lone "Opportunity" score with no structural peer would re-tempt the neophyte to read it as overall
suitability — the very problem this spec exists to prevent.**

---

## §7 — Dependencies

- **Structural Suitability gated on:** D-075 survival (the run, not yet authorised).
- **Clinical Opportunity needs:** SEER/GLOBOCAN prevalence, mortality/survival data, unmet-need
  curation — new public sources, none yet integrated.
- **Reuses:** `data/cancer_associations.csv` (expression breadth, D-053) — as a FLAGGED WEAK input,
  never the dominant term (§4).
- **Enabled-by / relates:** the census disease-stacking layer (`SPEC-phase2-census-sources.md` §3 is
  the same prevalence×lethality data); the confidence-demotion order (reserves the display slot).
- **Confound guard:** the orthogonality check (§4) — if the two axes correlate, Opportunity is
  leaking attention bias and must be rebuilt on burden, not breadth.
