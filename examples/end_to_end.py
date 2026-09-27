#!/usr/bin/env python3
"""Full pipeline: detection -> localization -> verification.

Uses a synthetic golden/observed pair to keep the example self-contained.
Replace `observed` with real captured responses to analyse your own circuit.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faultiva import Faultiva

f = Faultiva()
family = "opentitan_hmac_sha256"
print(f"characterized circuits : {f.families}")
print(f"signatures for {family} : {f.signature_count(family):,}")
print()

# --- clean circuit ---
golden = [f"resp_{i:04d}" for i in range(64)]
result = f.analyse(family, observed=golden, golden=golden)
print("CLEAN CIRCUIT")
print(result.summary())
print()

# --- faulty circuit: take a real signature from the dictionary ---
signature = f._sig[family][7]
sites = f.localize(family, signature)
print("FAULTY CIRCUIT (signature replayed from the dictionary)")
print(f"  signature      : {signature[:32]}...")
print(f"  candidate sites: {sites.size}")
print(f"  first few      : {sites[:8].tolist()}")
print()

rng = np.random.default_rng(1)
verdict, detail = f.verify(family, sites[:10], stuck_value=1,
                           key_bits=rng.integers(0, 2, 256),
                           message_bits=rng.integers(0, 2, 256))
print(f"  V1 verification: {verdict}")
for k, v in detail.items():
    print(f"    {k:<18} {v}")
print()
print("  out-of-scope check:", f.verify("secworks_aes", [0], 1, None, None)[0])
