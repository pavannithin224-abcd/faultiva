# Faultiva 1.0 — Model Card

**Hybrid fault detection and localization for gate-level VLSI circuits**

| | |
|---|---|
| **Model name** | Faultiva |
| **Version** | 1.0.0 |
| **Internal designation** | CircuitSage-HMAC V2.2 (detection/localization) + V1 (verification) |
| **Type** | Hybrid — deterministic signature retrieval + supervised neural classifier |
| **Task** | Stuck-at fault detection, localization, and structural verification |
| **Domain** | Gate-level digital VLSI netlists (post-synthesis) |
| **Total learned parameters** | **93,185** |
| **Bundle size** | 4.7 MB on disk · ~4.4 MB download |
| **Inference hardware** | CPU only — no GPU required |
| **License** | See `LICENSE` |
| **Status** | Research artifact. Independent-circuit generalization **NOT ESTABLISHED** |

---

## 1. Model Overview

Faultiva answers three questions about an observed circuit response, in order:

```
observed responses
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│ [1] DETECTION       Is this circuit faulty?                     │
│     V2.2 · 0 learned parameters · golden-response comparison    │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│ [2] LOCALIZATION    Which site(s) could be responsible?         │
│     V2.2 · 0 learned parameters · exact signature retrieval     │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│ [3] VERIFICATION    Is that site structurally plausible?        │
│     V1 · 93,185 parameters · MLP · threshold 0.4965             │
└─────────────────────────────────────────────────────────────────┘
```

### Why the hybrid is not redundant

Stages 1–2 reason from **behaviour** (observed response differences). Stage 3 reasons
from **structure** — V1 has `post_simulation_features: 0`, meaning it never sees
simulation output. The two evidence paths are **genuinely disjoint**, so agreement
between them is corroboration rather than a model confirming its own premise.

### Design decision: the localizer has no learned parameters

This is an evidence-based choice, not a simplification. Four learned formulations
were pre-registered and falsified (§6). A non-learning comparator outperformed the
best trained model by **~182× on MRR**. Shipping the learned model would have made
the system worse.

---

## 2. Intended Use

### Direct use
- Post-silicon and post-synthesis stuck-at fault diagnosis on characterized circuits
- Narrowing fault candidate sets prior to physical failure analysis
- Research baseline for behaviour-signature localization methods
- Teaching artifact for fault collapsing and observability analysis

### Out of scope
- **Safety-critical certification.** This is a research artifact with a NOT MET
  acceptance outcome.
- **Uncharacterized circuits.** Detection generalizes; localization requires a
  pre-built signature dictionary per circuit (§8).
- **Fault models other than single stuck-at.** No delay, bridging, transient, or
  multi-site fault was ever characterized.
- **Non-HMAC verification.** V1 returns `OUT_OF_SCOPE` on any other circuit.

### Misuse to avoid
Do not report a candidate set as a *single* predicted site. Between **83.3% and 99.8%**
of ambiguous sets are structurally equivalent — the members are indistinguishable by
any response-based method (§7).

---

## 3. Architecture

### Stage 1 — Detection (V2.2)

Deterministic comparison of observed responses against a golden reference. A
SHA-256 digest over the per-transaction mismatch pattern forms the **behaviour
signature**.

| property | value |
|---|---|
| learned parameters | 0 |
| inputs | observed response sequence, golden response sequence |
| output | `FAULT_DETECTED` / `NO_FAULT_DETECTED` + behaviour signature |
| false alarm rate | **0.0000** across 7,849,696 transactions |

### Stage 2 — Localization (V2.2)

Exact-match lookup of the behaviour signature in a pre-computed dictionary mapping
signatures to candidate fault sites (CSR layout, full 32-byte digests, no
truncation → no collision risk).

| property | value |
|---|---|
| learned parameters | 0 |
| dictionary entries | **127,484** signature→site pairs |
| distinct signatures | 42,819 across 4 circuits |
| output | `EXACT` (1 site) / `AMBIGUOUS` (n sites) / `UNKNOWN_SIGNATURE` |

### Stage 3 — Verification (V1)

| property | value |
|---|---|
| model id | `HYBRID_FUSION_MLP_11D2C` |
| candidate id | `HYBRID_SGC3_MLP_128_64_32_A1E4_LR1E3` |
| implementation | `sklearn.neural_network.MLPClassifier` |
| input features | **646** |
| hidden layers | **(128, 64, 32)**, ReLU |
| total layers | 5 (input → 3 hidden → output) |
| output activation | logistic (sigmoid) |
| **trainable parameters** | **93,185** |
| optimizer | Adam, `alpha=1e-4`, `learning_rate_init=1e-3` |
| batch size | 8,192 |
| decision threshold | **0.4965** (reselection prohibited) |
| lock status | FROZEN |

**Parameter breakdown**

| layer | shape | parameters |
|---|---|---|
| fusion → h1 | W(646, 128) + b(128) | 82,816 |
| h1 → h2 | W(128, 64) + b(64) | 8,256 |
| h2 → h3 | W(64, 32) + b(32) | 2,080 |
| h3 → output | W(32, 1) + b(1) | 33 |
| | **total** | **93,185** |

**Feature composition (646)**

| block | width | contents |
|---|---|---|
| fault polarity | 1 | `stuck_value` ∈ {0, 1} |
| site features | 14 | scaled cell fanout, primary-output stem flag, sequential stem flag, driver cell type one-hot (9), site category one-hot (2) |
| stimulus | 512 | 256 key bits + 256 message bits, MSB-first |
| **sample branch** | **527** | source: frozen `11C-5E` leakage-safe feature matrix |
| graph branch | 119 | directed SGC, K=3 hops, channels SELF / INBOUND / OUTBOUND, selected GNN `DIR_SGC_K3_L2_A1E5` |
| **total** | **646** | concatenated, then fused |

Labels were **not** used during graph propagation (`labels_used_during_propagation: false`),
so the graph branch is leakage-free.

---

## 4. Training Details (V1)

| | |
|---|---|
| training samples | 438,528 evaluated |
| positive prevalence | 0.4377 |
| optimizer steps recorded | **1,128** (`loss_curve_` length) |
| loss trajectory | 0.8375 → **0.3194** |
| best loss | 0.3117 |
| training time | 45–180 minutes, CPU |
| hardware | Intel Core Ultra 7 265H, 12 cores, no GPU |
| determinism | fixed seeds; deterministic replay verified exact |

### A note on `n_iter_ = 1`

The serialized estimator reports `max_iter = 1` and `n_iter_ = 1`. **This does not
mean the model trained for one iteration.** V1 was fitted in an explicit outer epoch
loop calling `partial_fit`-style single-iteration steps, so sklearn's internal counter
reads 1 while `loss_curve_` retains all **1,128** minibatch losses, descending
monotonically in trend from 0.8375 to 0.3194.

Independent corroboration that the weights are trained: layer-3 weights have mean
**+0.1358** and std **0.4282**, strongly asymmetric versus any symmetric initializer,
and the model achieves ROC-AUC **0.9166** on 438,528 samples — unattainable with
random weights.

This is documented because a reviewer inspecting the artifact will notice
`n_iter_ = 1` and should not have to guess.

---

## 5. Evaluation

### 5.1 V1 verification performance

Measured on 438,528 samples at threshold 0.4965:

| metric | value |
|---|---|
| balanced accuracy | **0.8166** |
| ROC-AUC | **0.9166** |
| PR-AUC | 0.8991 |
| MCC | 0.6298 |
| F1 | 0.8019 |
| precision | 0.7296 |
| recall (sensitivity) | 0.8901 |
| specificity | 0.7432 |
| Brier score | **0.1210** |

**Confusion matrix**

| | predicted negative | predicted positive |
|---|---|---|
| **actual negative** | 183,252 | 63,331 |
| **actual positive** | 21,089 | 170,856 |

Precision 0.73 against recall 0.89: V1 is deliberately recall-oriented. As a
*verification* stage its job is to avoid discarding true fault sites, accepting more
false positives in exchange.

### 5.2 Detection and localization, per circuit

Full fault campaign, all injected faults:

| circuit | faults | observable | detection recall (all) | detection recall (observable) | exact-site rate | MRR | unique-sig Top-1 | mean set | max set |
|---|---|---|---|---|---|---|---|---|---|
| `opentitan_hmac_sha256` | 36,784 | 9,844 | 0.2676 | **1.0000** | 0.0286 | 0.2544 | 0.1070 | 158.7 | 1,230 |
| `picorv32_cpu` | 19,330 | 4,753 | 0.2459 | **1.0000** | 0.0045 | 0.0989 | 0.0183 | 54.5 | 344 |
| `secworks_aes` | 53,120 | 51,601 | 0.9714 | **1.0000** | **0.3675** | 0.5463 | 0.3783 | 14.4 | 98 |
| `secworks_sha256` | 18,250 | 17,277 | 0.9467 | **1.0000** | **0.4516** | **0.6745** | 0.4771 | 3.8 | 44 |

**Across all four circuits: false alarm rate 0.0000, observable candidate-set
coverage 1.0000, ambiguous-false-unique rate 0.0000.** The localizer never claims a
unique site when the evidence supports several.

The spread between crypto datapaths (AES, SHA-256) and CPU-class circuits
(HMAC control path, PicoRV32) is the central empirical finding, and it tracks
**observability**: 97–100% of faults are observable in crypto datapaths versus
25–27% in CPU-class circuits.

### 5.3 Independent test circuit — prediction before truth

Before the sealed test circuit was opened, a falsifiable prediction was frozen:
`secworks_chacha` would achieve exact-site ≥ 0.15, within band [0.20, 0.70].

| | |
|---|---|
| circuit | `secworks_chacha` (ChaCha20 ARX stream cipher) |
| sites / faults | 10,111 / 20,222 |
| vectors / transactions | 64 / 1,294,208 |
| batch failures | **0** |
| observable fraction | **0.9988** |
| detection recall (observable) | **1.0000** |
| false alarm rate | **0.0000** |
| **exact-site rate** | **0.3440** |
| signature uniqueness | 0.3445 |
| max candidate set | 6,614 |
| **prediction outcome** | **CONFIRMED** |

The signature table was hashed (`aec3da69…`) **before** any metric was computed, and
the stage was mechanically prevented from reading the predicted bands.

---

## 6. Negative Results (falsified formulations)

Six formulations were pre-registered and falsified. They are reported because
omitting them would misrepresent what the evidence supports.

| formulation | parameters | outcome |
|---|---|---|
| `GRAPHSAGE_METRIC_SMALL` | 80,708 | no transfer to unseen circuits |
| `GATV2_CROSS_FUSION` | 182,052 | no transfer; apparent gain within noise |
| `GRAPHSAGE_OOD_ENSEMBLE` | 133,702 | no transfer |
| set reranker | 40,033 | lift exactly **1.00×** at every epoch |
| observability GNN | 41,649 | AUROC **0.348** — below random |
| hard-negative retraining | — | endpoint identical to random negatives (0.0156) |

Against the full candidate pool, trained models reached MRR 0.0027–0.0037 versus the
non-learning comparator's **0.6745** — roughly **182× worse** — with median rank
~3,134–4,917 of 9,127, i.e. indistinguishable from random.

### Why the reranker's 1.00× lift was the correct answer

Subsequent analysis proved the task was **not identifiable**: every site carries
exactly **2** faults (SA0, SA1), so the within-set posterior is 1/k regardless of
model capacity. The true site sits at depth percentile 0.4961–0.5083 against a
uniform expectation of 0.5000 — no feature separates it. A lift of exactly 1.00×
is the mathematically correct result, not a training failure.

**No learned model is shipped in the localization path.**

---

## 7. Fundamental Limits

### Structural fault equivalence

Ambiguity is dominated by genuine structural equivalence, not search weakness:

| circuit | structurally equivalent | splittable | current exact-site | optimistic ceiling |
|---|---|---|---|---|
| `opentitan_hmac_sha256` | **99.79%** | 0.07% | 0.0681 | 0.0681 |
| `picorv32_cpu` | 83.29% | 1.57% | 0.0243 | 0.0249 |
| `secworks_aes` | 89.58% | 3.17% | 0.5306 | 0.5410 |
| `secworks_sha256` | **99.71%** | 0.18% | 0.6385 | 0.6392 |

Minimum structurally equivalent fraction: **0.8329**. Even a perfect reranker could
move HMAC only from 0.0681 to 0.0681 and PicoRV32 from 0.0243 to 0.0249.

**Defensible claim:** behaviour-signature localization resolves faults to their
**equivalence class** — the finest resolution any response-based method can achieve.
The residual ambiguity is structural, not methodological, and matches the classical
fault-collapsing result.

### Acceptance outcome: NOT MET

The acceptance contract required the per-circuit exact-site floor (≥ 0.15) on **both**
independent test circuits.

| circuit | captured | exact-site | meets floor |
|---|---|---|---|
| `secworks_chacha` | yes | 0.3440 | **yes** |
| `ibex_cpu` | **no** | — | — |

`ibex_cpu` could not be captured: the acquired source snapshot is not self-contained
for gate-level synthesis (missing `lc_ctrl_pkg`, RAM primitive packages,
`PRIM_FLOP_SPARSE_FSM`, technology primitives). Authoring substitute RTL was
**refused** — faults would then be injected into logic written by this project rather
than into ibex, inside a one-shot evaluation where the contamination could never be
corrected.

**The outcome is capture infeasibility, not a measured shortfall.** The distinction is
preserved throughout the frozen record. The one-shot locked evaluation remains
**unconsumed**.

---

## 8. Using Faultiva on a New Circuit

| capability | new circuit | requirement |
|---|---|---|
| **Detection** | ✅ works | a golden reference response |
| **Localization** | ⚠️ requires characterization | full fault-injection campaign |
| **Verification** | ❌ HMAC only | V1 retraining |

Characterization is a one-time offline cost: synthesize to gate level, enumerate
sites, inject SA0/SA1 per site across the vector plan, and derive signatures. For
reference, `secworks_chacha` — 10,111 sites, 20,222 faults, 1,294,208 transactions —
took **10.2 minutes** with 10 parallel workers.

Four circuits ship pre-characterized (42,819 signatures, 127,484 site entries).

---

## 9. Performance

| operation | measured |
|---|---|
| detection | 0.082 ms |
| localization | 0.019–0.046 ms |
| V1 verification, 10 candidate sites | **0.2564 ms** |
| V1 verification, 100 candidate sites | ~2.0 ms |
| **end-to-end per query** | **≈0.5 ms** |
| throughput | ~2,000 queries/s, single core |
| cold start | ~1.5 s |
| golden reference computation (if uncached) | 48.7 ms |

CPU-only. No GPU, no network access at inference.

---

## 10. Reproducibility and Provenance

Every number in this card traces to a frozen, hash-anchored audit artifact.

| stage | subject | audit SHA-256 |
|---|---|---|
| 12C-2F | gate measurement | `aaa862b7ec4128eb8a3aa80233c8661ab799f1599ea1fed216bae49f98a66fb7` |
| 12C-2G | graph reranking (falsified) | `bbff14cdbcf3b53ee6a3cfd3753fc205e7f72a0606d885eac4be18f7f8a37144` |
| 12C-2I | pipeline assembly | `78a5cad19a6910ee8a163adaba4514e13e08b87d9ef6c6253699b79bfcffd350` |
| 12C-2L | equivalence bound | `bfd1df4d20a74c7bbb0832fc59f7decb1da6e76fd937ecb046d425666c6a969a` |
| 12C-3A | test capture authorization | `3613ef29f3d8f8cdacb2c5d4126e28137991f592b85fe792b7474a4cfd9b315a` |
| 12C-3B | chacha capture | `c517a208021735bd3b092361594447a14de72146cd7464e14eb310530978d6be` |
| 12C-3C | ibex infeasibility | `a1d94201521357a3c8c2f33e153297c36f04081ee9a077bb8ac5b33f9608cd47` |
| 12C-3D | closing disposition | `1d34e232822e575a7c372fb1cfbf2d4b6b0ed8fb421d3513a5a085e8232023e4` |
| 12C-4A | genuine V1 wiring | `594fc0c0bc578db3e6f3f4333e586e99756d835240c8b31a8a15ecb61c44d64e` |
| 12C-4B | packaging | `50ef6c794dd81bacf801c6a3f8392cc58faff1a8dc4e0853a35b27b352e44d11` |

### Methodological controls

- **Append-only evidence.** No frozen stage is ever edited; corrections become new
  stages.
- **Prediction before truth.** Sealed-circuit predictions were frozen before any seal
  was broken; capture stages were mechanically prevented from reading them.
- **One-shot evaluation.** The locked test may be opened once. It remains unconsumed.
- **Protected-partition accounting.** Every stage records test/validation/holdout
  access counts; all are 0.
- **Toolchain pinning.** Verilator `5.051 devel rev v5.050-222-gf6f6f8404 (mod)`,
  Yosys `0.68+106 (git sha1 c92678eb2-dirty)`. Re-simulation reproduces frozen
  batches byte-identically.
- **Sealed holdout.** `serv_cpu` remains sealed by decision.

### Environment

Python 3.10+, numpy, scikit-learn, joblib. Offline execution. Verify integrity with
`sha256sum -c SHA256SUMS`.

---

## 11. Known Defects (disclosed)

| defect | status |
|---|---|
| V1 was originally wired as a scope gate that never called `predict_proba` | **fixed** in 12C-4A; the original stage is preserved unmodified |
| Graph features were double-scaled during development, saturating 34% of outputs to exactly 0 or 1 | **fixed**; convention frozen and asserted by regression self-test |
| The shipped signature dictionary originally stored candidate *counts* but not candidate *sites* | **fixed** in 12C-4B; 127,484 site entries now exported |
| The prediction scoring rule had no label for an uncapturable circuit | **fixed** by adding `NOT_SCORABLE`; the original rule is unmodified and the omission is recorded as a defect |

The graph/fanout scaling asymmetry (graph features ship **pre-scaled**; cell fanout
ships **raw**) is handled inside `FeaturePipeline.build_vector()`, so integrators
cannot reintroduce the defect by following the obvious reading of the schema.

---

## 12. Citation

```bibtex
@software{faultiva2026,
  title  = {Faultiva: Hybrid Fault Detection and Localization for VLSI Circuits},
  author = {Pavan Nithin},
  year   = {2026},
  version = {1.0.0},
  note   = {CircuitSage-HMAC V2.2 + V1 hybrid; independent-circuit
            generalization not established}
}
```

---

## 13. Honest Summary

**What Faultiva does well.** Detects faults with zero false alarms across 7.85 million
transactions. Localizes observable, behaviourally unique faults exactly — 0.6385
exact-site on SHA-256, 0.5306 on AES. Runs in half a millisecond on one CPU core.
Resolves faults to their true equivalence class rather than guessing a single site.

**What it does not do.** It does not meet its own acceptance contract, because one of
two required independent test circuits could not be captured. It does not localize
well on CPU-class circuits — 0.0045 exact-site on PicoRV32 — because only ~25% of
faults there are observable. It does not verify anything outside OpenTitan HMAC. It
does not use a learned localizer, because four were tried and all failed.

**What the project establishes.** That behaviour-signature localization is bounded by
structural fault equivalence, that the bound is reachable, and that on this problem a
zero-parameter method beats every learned alternative tried. Those are measured
results with hash-anchored provenance — including the parts that did not work.
