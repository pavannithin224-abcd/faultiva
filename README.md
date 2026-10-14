<div align="center">

# Faultiva

### Find the broken gate in a chip from its output alone.

Detection, localization, and structural verification, as a Windows app or a 4.7 MB Python package. No GPU, no network, half a millisecond per query.

<p>
<a href="https://github.com/pavannithin224-abcd/faultiva/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/pavannithin224-abcd/faultiva/actions/workflows/ci.yml/badge.svg"></a>
<img alt="python" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776ab?style=flat-square&logo=python&logoColor=white">
<a href="https://doi.org/10.5281/zenodo.22987539"><img alt="DOI" src="https://zenodo.org/badge/DOI/10.5281%2Fzenodo.22987539.svg"></a>
<img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square">
<img alt="gpu" src="https://img.shields.io/badge/GPU-not%20required-0b6e4f?style=flat-square">
</p>

<img src="docs/app-overview.png" alt="The Faultiva desktop app: a fault catalogue of 42,819 signatures over 127,484 site entries, 93,185 learned parameters, four characterized circuits, typical diagnosis around 10 ms, and a detected toolchain of yosys 0.68+106 and verilator 5.051." width="92%">

</div>

---

## Two ways in

|  | |
|---|---|
| **[Download the app](../../releases/latest)** | Windows installer, 46 MB. Double-click, pick a circuit, upload a capture. No Python, no setup, no admin rights. |
| **Use the library** | `git clone` and `pip install -r requirements.txt`. Same engine, scriptable, 5.7 MB. |

The app and the library run identical code. The desktop build is a window around the same package, and both were
checked to produce byte-identical results on all four shipped circuits.

---

## What it does

Give it the responses a circuit produced and the responses it should have produced. It tells
you whether the circuit is faulty, which gates could be responsible, and whether that answer
holds up against an independent structural model.

```python
from faultiva import Faultiva

f = Faultiva()
result = f.analyse("opentitan_hmac_sha256", observed, golden)

print(result.summary())
print(result.candidate_sites)     # the gates that could produce this behaviour
```

On the HMAC circuit that narrows **36,784 possible fault sites to a handful** — often to one.

<div align="center">
<img src="docs/app-analyse.png" alt="Analysing a capture: 38 of 64 test vectors differ from the fault-free reference, and the signature resolves to one candidate, an SA0 fault on gate 9935." width="92%">
<br>
<sub>A capture from the OpenTitan HMAC core: 38 of 64 vectors differ, and the signature resolves to a single gate.</sub>
</div>

<div align="center">
<img src="docs/pipeline.png" alt="Three-stage pipeline: detection and localization reason from behaviour with zero learned parameters; verification reasons from structure using a 93,185-parameter MLP." width="100%">
</div>

Stages 1 and 2 read **behaviour**, what the circuit actually output. Stage 3 reads
**structure**: the netlist topology around each candidate, with no simulation data at all.
Two independent paths to the same question, so when they agree it means something.

---

## Install

**The app.** Download `Faultiva-Setup-1.0.0.exe` from [Releases](../../releases/latest) and run it.
It installs per-user, so Windows never asks for admin rights. Verify the download first if you like:

```powershell
Get-FileHash Faultiva-Setup-1.0.0.exe -Algorithm SHA256
```

**The library**

<div align="center">
<img src="docs/demo.png" alt="The same engine from a terminal: a fault detected and localized, a fault-free circuit cleared, and OUT_OF_SCOPE returned on a circuit the verifier was not trained on." width="88%">
</div>

```bash
git clone https://github.com/pavannithin224-abcd/faultiva.git
cd faultiva
pip install -r requirements.txt

sha256sum -c SHA256SUMS      # verify the bundle
python examples/predict_one.py
python examples/end_to_end.py
```

Python 3.10+, numpy, scikit-learn, joblib. Everything needed is in the repository. There is no second download and no model to fetch.

---

## What ships

| circuit | signatures | uniquely localizable | largest candidate set |
|---|---|---|---|
| `secworks_sha256` | 11,654 | 70.7% | 973 |
| `secworks_aes` | 28,189 | 69.3% | 1,519 |
| `opentitan_hmac_sha256` | 2,505 | 42.0% | 26,940 |
| `picorv32_cpu` | 471 | 18.5% | 14,577 |

**127,484** signature-to-site entries in total, built from full stuck-at campaigns across
**7,849,696** transactions with **zero false alarms**.

| | |
|---|---|
| detection | 0.082 ms |
| localization | 0.019–0.046 ms |
| verification (10 candidates) | 0.256 ms |
| **end to end** | **≈0.5 ms** · ~2,000 queries/s on one core |

---

## Bring your own circuit

The four circuits above ship ready. For anything else, Faultiva builds the signature
dictionary itself. Point it at your Verilog and it synthesizes, injects every single
stuck-at fault, simulates, and catalogues what each one did.

In the app this is the **Add your circuit** page: choose your `.v` files and it reads the
port names off the top module, so the clock, reset, data ports and their widths fill
themselves in. You supply the start and done signals, since no naming rule can guess those
reliably. Progress is reported per phase with a running clock, and the run can be cancelled
and resumed, and completed batches are kept.

<div align="center">
<img src="docs/app-addcircuit.png" alt="Adding a circuit: siphash_core.v is read and every port listed, with clock, reset, data ports and widths filled in from the design." width="92%">
<br>
<sub>Every port is read off the design. Held constants are prefilled at zero and flagged, because a round count left at zero stops the circuit ever finishing.</sub>
<br><br>
<img src="docs/app-preflight.png" alt="Preflight on siphash_core: 3,997 fault sites, 7,994 single stuck-at faults, 63 simulation batches, estimated 32 minutes on 10 workers." width="92%">
<br>
<sub>What the run will cost, before you start it. Checked in 1.4 seconds.</sub>
</div>

From the command line:

```bash
python -m faultiva.characterize --config my_core.yaml --check    # validate, no simulation
python -m faultiva.characterize --config my_core.yaml --workers 10
```

This needs **Yosys and Verilator**, which exist on Linux and on Windows inside WSL. The app
detects them and tells you what is missing rather than failing midway; it prefers a pinned
OSS CAD Suite over a distribution package, because a catalogue is only comparable to the
shipped ones when it comes from the same toolchain.

Characterization is a one-time offline step. Diagnosis afterwards is the same
sub-millisecond lookup as for the bundled circuits.

<details>
<summary>A worked example, measured end to end</summary>

<br>

[`secworks/siphash`](https://github.com/secworks/siphash) — a core Faultiva had never seen,
characterized through the app on an eight-core laptop:

| | |
|---|---|
| fault sites | 3,997 |
| single stuck-at faults | 7,994 |
| test vectors | 64 |
| wall-clock | ~3 minutes |
| uniquely localizable | 65.7% |

That 65.7% is the number to look at before trusting the result. It sits with the crypto
cores rather than the CPU, which is what you would expect from a hash core, and it tells
you directly how often a diagnosis will come back as one gate rather than a set.

A circuit's config is a short YAML file naming its clock, reset, handshake signals, data
ports, and any pins held at a fixed value. [`docs/CHARACTERIZATION.md`](docs/CHARACTERIZATION.md)
has the full schema and a runnable example.

</details>

---

## Four things to know before you rely on it

**1 · Localization returns a set, not always a single gate.**

Faults that produce identical output cannot be told apart by any method that only watches
output. When Faultiva returns six candidates, those six are usually genuinely
indistinguishable. 83–99% of ambiguous sets are structurally equivalent. Treat the set as
the answer, not as a ranked guess.

**2 · Verification covers HMAC only.**

Stage 3's model was trained on `opentitan_hmac_sha256`. On anything else it returns
`OUT_OF_SCOPE` rather than a number it cannot justify. Detection and localization work on all
four shipped circuits and on any circuit you characterize yourself.

**3 · Stage 3 disagrees with stage 2 about 29% of the time, by design.**

Across 300 localized HMAC cases, V1 verification returned `VERIFIED` 71% of the time and
`FLAGGED` the rest. That is the point of using disjoint evidence: a behaviourally distinctive
fault at a structurally hard-to-detect site *should* produce disagreement. Read `FLAGGED` as
"the two paths don't corroborate here", not as "localization failed".

**4 · A new circuit needs characterizing before localization works.**

Detection works anywhere you have a golden reference. Localization needs a signature
dictionary, which means a full fault-injection campaign: offline, one time, minutes to hours
depending on size. For scale: `secworks_chacha`, 10,111 sites and 20,222 faults, took 10.2
minutes on 10 cores. Four circuits ship ready.

<details>
<summary>Scope boundaries and the full evidence trail</summary>

<br>

Faultiva handles **single stuck-at faults** (SA0/SA1). Delay, bridging, transient, and
multi-site faults were never characterized, so results for them are undefined rather than merely uncertain.

The localizer has **zero learned parameters**. That is an evidence-based choice: six learned
formulations were pre-registered and falsified during development, and a non-learning
comparator beat the best of them by roughly 182× on MRR. Within-set localization was then
proven mathematically unidentifiable. The full record, including the failures, is in the
[**V2 study repository**](https://github.com/pavannithin224-abcd/circuitsage-vlsi-fault-detection-v2).

Independent-circuit generalization is **not established**: one of two sealed test circuits
met its pre-registered floor, the other could not be synthesized from its published source.

This is a research artifact. Do not use it as the sole basis for certification or
manufacturing decisions.

</details>

---

## Model card

[**`docs/MODEL_CARD.md`**](docs/MODEL_CARD.md) has the full specification: architecture,
parameter tables, training details, per-circuit evaluation, measured limits, and the audit
hash for every number.

| | |
|---|---|
| total learned parameters | 93,185 (verification only) |
| verification model | MLP 646 → 128 → 64 → 32 → 1, threshold 0.4965 |
| balanced accuracy | 0.8166 · ROC-AUC 0.9166 · Brier 0.1210 |
| localization | exact signature retrieval, 0 parameters |

---

## How it works

Detection compares the observed responses against the golden ones and reduces the difference
to a **behaviour signature** — a digest over four channels: the response XOR, the cycle
delta, and the timeout and protocol-error flags. Localization looks that signature up in a
dictionary built by injecting every possible stuck-at fault and recording what each one did,
so a match returns exactly the gates capable of producing that behaviour. Verification then
scores each candidate with a model that has never seen simulation output, only netlist
structure.

The interesting property is what happens when localization is ambiguous: the candidate set
*is* the fault equivalence class. There is no better answer available from output alone.

---

## Related work

| | |
|---|---|
| [**CircuitSage V2**](https://github.com/pavannithin224-abcd/circuitsage-vlsi-fault-detection-v2) | the study behind this tool: method, pre-registered contracts, audit trail, negative results |
| [**CircuitSage V1**](https://github.com/pavannithin224-abcd/circuitsage-hmac-fault-detection) | the original HMAC detection model, reused here as stage 3 |

---

## Citation

```bibtex
@software{faultiva2026,
  title   = {Faultiva: Hybrid Fault Detection and Localization for VLSI Circuits},
  author  = {Pavan},
  year    = {2026},
  version = {1.0.0},
  url     = {https://github.com/pavannithin224-abcd/faultiva}
}
```

## License

Apache-2.0, see [`LICENSE`](LICENSE). Third-party RTL used to build the signature
dictionaries retains its upstream licensing; see the V2 repository for per-project
attribution.

---

<div align="center">
<sub>

Built as an undergraduate major project · [report a defect](https://github.com/pavannithin224-abcd/faultiva/issues) · [contributing](CONTRIBUTING.md)

</sub>
</div>