# Worked example: characterize a circuit from scratch

Runs the whole bring-your-own-circuit flow on a circuit small enough to read in
one sitting. Takes about ten seconds.

## Run it

```bash
cd /path/to/faultiva

# validate the config, no simulation
python -m faultiva.characterize --config examples/characterize_demo/demo_core.yaml --check

# characterize
python -m faultiva.characterize --config examples/characterize_demo/demo_core.yaml
```

Needs Yosys and Verilator 5.x on `PATH`, or `FAULTIVA_YOSYS` /
`FAULTIVA_VERILATOR` pointing at them.

## Expected output

```
[1/7] synthesizing demo_core with yosys
[2/7] enumerating fault sites
      209 sites, 418 faults, 6 cell types
[3/7] instrumenting netlist
[4/7] building simulator with verilator
[5/7] capturing fault-free baseline
      8 vectors, 8 distinct responses, 0 timeout(s)
[6/7] campaign: 4 batches, 0 already done, 4 to run, 10 workers
      4/4 batches
[7/7] writing catalogue
      199 signatures, 34.2% unique, largest set 20
```

Site and signature counts depend on your Yosys version and the synthesis recipe
— a different flow gives a different but equally valid gate-level circuit. The
shape of the result should match.

## Then diagnose it

```python
from pathlib import Path
from faultiva import Faultiva

f = Faultiva()
print(f.families)                      # demo_core now appears
print(f.is_user_circuit("demo_core"))  # True
print(f.circuit_info("demo_core"))     # counts, hashes, caveats
```

The dashboard (`python app/app.py`) lists it alongside the four bundled
circuits. Detection and localization work identically; V1 verification reports
`OUT_OF_SCOPE`, because V1 was trained on `opentitan_hmac_sha256` only.

## Reading the numbers

**34.2% uniquely localizable** means roughly a third of faults resolve to a
single site. The rest land in a candidate set because several faults produce
byte-identical output on all 8 vectors — they are structurally equivalent, and no
method that only observes responses can separate them.

A real crypto core with 64 vectors typically reaches 60–70%. This demo is small
and uses 8 vectors, so more faults collide.

**The largest set has 20 faults.** Those sit on a shared path where a fault
anywhere produces the same observable behaviour.

## Next

Copy `demo_core.yaml`, point it at your own RTL, and adjust the signal names.
`docs/CHARACTERIZATION.md` covers the optional fields, the supported interface,
and the two honest limitations of a user-built catalogue.
