# Faultiva — honest limits

Read this before drawing conclusions from any output.

## 1. Independent generalization is NOT ESTABLISHED

The acceptance contract required the per-circuit floor on **both** independent
test circuits. One (`secworks_chacha`) was captured and **met** its floor. The
other (`ibex_cpu`) could **not be captured** — the acquired source snapshot is not
self-contained for gate-level synthesis, and authoring substitute RTL was refused
because faults would then be injected into logic written by this project.

**Acceptance outcome: NOT MET**, by capture infeasibility rather than a measured
shortfall. The distinction matters and is preserved in the frozen record.

## 2. Localization resolves to an equivalence class, not always a site

Between **83.3% and 99.8%** of ambiguous candidate sets are *structurally
equivalent* — the faults are indistinguishable from any response, by any method.
When Faultiva returns 6 candidates, that is usually the true resolution limit,
not a weakness in the search.

Optimistic per-family ceilings (12C-2L):

| circuit | current exact-site | optimistic maximum |
|---|---|---|
| `opentitan_hmac` | 0.0681 | 0.0681 |
| `picorv32_cpu` | 0.0243 | 0.0249 |
| `secworks_aes` | 0.5306 | 0.5410 |
| `secworks_sha256` | 0.6385 | 0.6392 |

## 3. Learned models were tried and failed

Four independent formulations (metric learning, GATv2 cross-fusion, OOD ensemble,
observability prediction) were pre-registered and **falsified**. A non-learning
comparator beat the best trained model by ~182x on MRR. The observability GNN
reached AUROC **0.348** — below random.

**The shipped localizer has zero learned parameters.** This is an evidence-based
choice, not a shortcut.

Within-collision-set localization was additionally proven **not identifiable**:
every site carries exactly 2 faults, so the posterior is 1/k and no model can do
better than chance inside a set.

## 4. V1 verification is HMAC-only

V1 was trained on `opentitan_hmac_sha256`. Other circuits return **OUT_OF_SCOPE**.
Balanced accuracy **0.8166**, Brier **0.1210**, threshold **0.4965**
(reselection prohibited).

## 5. Single stuck-at faults only

The catalog contains SA0 and SA1 exclusively. Delay, bridging, transient and
multi-site faults were never characterized. Nothing here supports claims about them.

## 6. New circuits require characterization

Detection works on any circuit with a golden reference. **Localization requires a
pre-built signature dictionary**, which means a full fault-injection campaign —
hours of offline simulation per circuit. Four circuits ship characterized.

## What IS established

- detection with **0 false alarms** across 7,849,696 transactions
- exact localization on observable, behaviourally unique faults
- a crypto-class prediction frozen **before** the seal was broken and then
  confirmed: `secworks_chacha` predicted [0.20, 0.70], measured
  **0.3440**
- catalog-scale effect: same circuit, same faults, catalog 4x larger →
  exact-site fell **19.2x**
