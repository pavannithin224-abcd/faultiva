#!/usr/bin/env python3
"""Localize a captured response set — the path a real user takes.

A capture records four channels per test vector:

    response  32 bytes the circuit produced
    timeout   1 if that vector did not complete in its cycle budget
    protocol  1 if the transaction violated the bus protocol
    cycles    completion cycle count

Faultiva XORs the responses against the shipped golden baseline, hashes all
four channels into a behaviour signature, and looks that signature up in the
fault catalogue.

Real captures from the fault campaign ship in data/faultiva_example_captures.npz
so this runs without a simulator. Point `load_capture` at your own harness
output to analyse a real circuit.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faultiva import Faultiva

f = Faultiva()
captures = np.load(Path(f.root) / "data/faultiva_example_captures.npz",
                   allow_pickle=True)


def load_capture(family: str) -> dict:
    """Return one recorded capture. Replace with your own harness reader."""
    return {
        "response": captures[f"{family}__response"],
        "timeout": captures[f"{family}__timeout"],
        "protocol": captures[f"{family}__protocol"],
        "cycles": captures[f"{family}__cycles"],
    }


def analyse(family: str, capture: dict) -> None:
    golden = f.golden(family)
    n = f.vectors(family)

    differing = int((capture["response"] != golden).any(axis=1).sum())
    verdict = "FAULT_DETECTED" if differing else "FAULT_FREE"
    print(f"  [1] DETECTION     {verdict}")
    print(f"                    {differing} of {n} vectors differ from golden")
    if not differing:
        return

    key = f.signature(family, capture["response"], golden,
                      cycle_delta=capture["cycles"],
                      timed_out=capture["timeout"],
                      protocol_error=capture["protocol"])
    sites = f.localize(family, key)
    if sites.size == 0:
        print("  [2] LOCALIZATION  UNKNOWN_SIGNATURE")
        print("                    this behaviour is not in the catalogue;")
        print("                    Faultiva does not guess a location")
        return

    kind = "EXACT" if sites.size == 1 else "AMBIGUOUS"
    print(f"  [2] LOCALIZATION  {kind}   {sites.size} candidate site(s)")
    print(f"                    sites {sites[:8].tolist()}"
          f"{' ...' if sites.size > 8 else ''}")
    if sites.size > 1:
        print(f"                    these {sites.size} faults produce identical")
        print("                    output and cannot be separated further")


print("=" * 66)
print("REAL CAPTURES FROM THE FAULT CAMPAIGN")
print("=" * 66)
for family in [str(x) for x in captures["families"]]:
    print(f"\n{family}   ({f.signature_count(family):,} catalogued signatures)")
    analyse(family, load_capture(family))

print()
print("=" * 66)
print("A CAPTURE THE CATALOGUE HAS NEVER SEEN")
print("=" * 66)
family = "secworks_sha256"
golden = f.golden(family)
rng = np.random.default_rng(7)
response = golden.copy()
for v in rng.choice(f.vectors(family), size=5, replace=False):
    response[v] ^= rng.integers(1, 256, size=32, dtype=np.uint8)

print(f"\n{family}   (randomly corrupted bytes, not a real fault)")
analyse(family, {
    "response": response,
    "timeout": np.zeros(f.vectors(family), dtype=np.uint8),
    "protocol": np.zeros(f.vectors(family), dtype=np.uint8),
    "cycles": np.zeros(f.vectors(family), dtype=np.int32),
})
print()
print("Detection works on any circuit with a golden reference.")
print("Localization only resolves behaviour the catalogue has recorded.")
