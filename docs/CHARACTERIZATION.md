# Characterizing your own circuit

Faultiva diagnoses faults by looking up a circuit's **behaviour signature** in a
catalogue. The four bundled circuits already have catalogues. To diagnose your
own design you build one first — that is characterization.

It is a one-time offline step per circuit. Afterwards every diagnosis is a
sub-millisecond lookup.

```
your RTL + config.yaml
        |
        v
  synthesize -> enumerate sites -> instrument -> simulate every fault
        |
        v
  user_circuits/<your_circuit>/   <- appears in the dashboard
```

## Requirements

| tool | why | note |
|------|-----|------|
| [Yosys](https://github.com/YosysHQ/yosys) | synthesis to a gate-level netlist | 0.3x or newer |
| [Verilator](https://verilator.org) | fault simulation | **5.x required** (`--binary --timing`) |
| PyYAML | reading the config | `pip install pyyaml` |

Both tools come bundled in [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build),
which is the easiest way to get a matching pair. If they are not on `PATH`:

```bash
export FAULTIVA_YOSYS=/path/to/yosys
export FAULTIVA_VERILATOR=/path/to/verilator
```

## Quick start

```bash
# 1. validate your config without simulating anything
python -m faultiva.characterize --config my_core.yaml --check

# 2. characterize (minutes to hours depending on circuit size)
python -m faultiva.characterize --config my_core.yaml

# 3. your circuit now appears in the dashboard circuit list
python app/app.py
```

## Supported interface

**One interface pattern is supported: `simple_handshake`.**

```
reset -> drive inputs -> pulse or raise start -> wait for done -> read output
```

That covers a great many crypto cores and fixed-latency datapaths. It does
**not** cover FIFO interfaces, bus protocols (AXI, Wishbone), multi-beat
streaming, or designs that fetch from instruction memory. Those are rejected
with an explicit message rather than driven incorrectly.

Worth stating plainly: `opentitan_hmac_sha256`, one of the four bundled
circuits, needed a hand-written SystemVerilog FIFO adapter and **would itself be
rejected** by this schema. The tool handles the common case, not every case.

## The config file

```yaml
circuit: my_aes_core          # lowercase id; becomes a directory name
top: aes_core                 # top module in your RTL
rtl:                          # source files, in any order
  - rtl/aes_core.v
  - rtl/aes_sbox.v

clock: clk
reset: {name: reset_n, active_low: true}

protocol: simple_handshake
start: {name: init, pulse: true}     # pulse: one cycle, then released
done:  ready                         # or {name: ready, active_low: true}

inputs:                              # stimulus ports, driven per vector
  - {name: key,   width: 256}
  - {name: block, width: 128}
output: {name: result, width: 128}   # the response that gets compared

vectors: 64                          # test patterns per fault
cycle_budget: 200                    # give up after this many cycles
```

### Optional fields

```yaml
constants:                    # config pins held fixed all campaign
  - {name: mode,    width: 1, value: 1}
  - {name: keylen,  width: 1, value: 0}

include_dirs: [rtl/include]   # -I for the Verilog frontend
defines: {SYNTHESIS: 1}       # -D for the Verilog frontend

seed: "my-experiment-1"       # changes the derived stimulus
abc_gates: "AND,OR,XOR,MUX"   # gate library for technology mapping
opt_clean: true               # drop unused cells after mapping

synthesis_script: |           # full control; must contain all three tokens
  read_verilog {sources}
  hierarchy -check -top {top}
  proc; opt; memory; opt; techmap; opt
  abc -g AND,OR,NAND,NOR,XOR,MUX
  write_json {json_out}
```

**The synthesis recipe changes the site count.** A more aggressive flow produces
a smaller netlist with fewer fault sites — a different but equally valid
gate-level circuit. Whichever recipe you use is recorded in `manifest.json` and
the exact script is written to `work/logs/yosys_script.txt`, so a catalogue is
always traceable to the flow that produced it.

## What gets produced

```
user_circuits/my_aes_core/
  catalogue.npz    signature -> candidate sites and polarities
  golden.npz       fault-free reference responses and cycle counts
  sites.npz        cell names, cell types, output ports
  manifest.json    counts, hashes, toolchain, caveats
  work/            netlists, testbench, per-batch CSVs, checkpoint
```

`work/` can be deleted afterwards; the four files above are the catalogue.
Keep `work/obj_dir/sim` if you want to re-simulate individual faults later.

## How it works

| phase | what happens |
|-------|--------------|
| 1 synthesize | Yosys maps your RTL to generic gates. Faults live on wires between gates, so this is mandatory. |
| 2 enumerate | every distinct driven cell-output bit becomes a fault site |
| 3 instrument | a MUX at each site output lets that wire be forced to 0 or 1 at runtime — one build serves every fault |
| 4 build | Verilator compiles the instrumented netlist plus a generated testbench |
| 5 baseline | fault-free sweep. **This is the golden reference.** |
| 6 campaign | every site × {SA0, SA1} × every vector, batched 64 sites at a time, run in parallel |
| 7 catalogue | the four observable channels per fault are hashed into one signature |

### The four channels

Each vector records four measurements, not one, because a fault need not corrupt
the output data:

| channel | catches |
|---------|---------|
| response bytes | wrong data |
| timeout flag | the circuit hung and never asserted `done` |
| protocol flag | `done` was already asserted while idle |
| cycle count | correct answer, wrong timing |

All four across all vectors are hashed into a 32-byte signature. Lookup is exact
— either the behaviour is in the catalogue or the result is
`UNKNOWN_SIGNATURE`. There is no nearest-match guessing.

## Deterministic stimulus

Test vectors are derived, not sampled:

```
bits = SHA256(circuit_id \0 port_name \0 vector_index \0 seed)
```

The same config always produces the same vectors on any machine, so a catalogue
is reproducible without shipping a vector file. Change `seed` to get a different
test set.

## Two honest limitations

**1 · The golden reference comes from your own netlist.**

For a third-party circuit there is no independent software model of the
algorithm, so phase 5 simulates your design with injection disabled and calls
that correct. Localization only needs *differences* from fault-free behaviour,
so this is sufficient for diagnosis — but a functional bug in your design would
be inherited by the reference rather than detected.

The four bundled circuits were characterized against independent oracles (AES,
SHA-256, and a RISC-V emulator), so they carry that extra cross-check. Yours
will not. This is recorded as `golden_source` in the manifest.

**2 · V1 verification stays OUT_OF_SCOPE.**

The 93,185-parameter structural verifier was trained on
`opentitan_hmac_sha256` only. Rather than score a circuit it has never seen, it
declines. Detection and localization work normally; stage 3 reports
`OUT_OF_SCOPE`.

## Runtime

Dominated by phase 6, which is `sites × 2 × vectors` simulated transactions.

| circuit | sites | vectors | workers | time |
|---------|-------|---------|---------|------|
| small demo | 209 | 8 | 6 | ~10 s |
| SHA-256 core | 5,848 | 64 | 10 | ~9 min |

Scale roughly linearly from there. Everything is checkpointed after each batch,
so an interrupted run resumes without re-simulating; a partial batch is
discarded and redone whole rather than appended to.

```bash
--workers N      parallel simulators (default 10)
--no-resume      ignore an existing checkpoint
--out DIR        catalogue location (default <package>/user_circuits)
--work DIR       scratch location
```

## Troubleshooting

**`protocol 'x' is not supported`** — only `simple_handshake` works. Your design
needs a hand-written testbench.

**`synthesis failed`** — check `work/logs/yosys_synth.log`. Usually a missing
source file, a wrong top module name, or an unsupported SystemVerilog construct.
Yosys does not accept everything a simulator does.

**`simulator build failed`** — check `work/logs/verilator_build.log`. Verilator
5.x is required; 4.x rejects `--binary` and `--timing`.

**`every fault-free transaction timed out`** — the design never asserted `done`.
Check the `done` signal name and polarity, whether `start` should be pulsed or
held, and raise `cycle_budget`.

**`port name(s) collide with the fault-injection ports`** — your design uses
`fi_enable_i`, `fi_site_onehot_i` or `fi_stuck_value_i`. Rename them.

**Site count differs from what you expected** — see the synthesis note above.
The recipe determines it.

## A complete worked example

See `examples/characterize_demo/` for a small circuit and its config, runnable
in about ten seconds.
