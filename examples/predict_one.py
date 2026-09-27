#!/usr/bin/env python3
"""Smallest possible working example: build one V1 vector and score it."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faultiva import Faultiva

f = Faultiva()
rng = np.random.default_rng(0)

node = 1000
key = rng.integers(0, 2, 256)
msg = rng.integers(0, 2, 256)

vector = f.features.build_vector(node, stuck_value=1,
                                 key_bits=key, message_bits=msg)
print(f"feature vector : {vector.shape[0]} features")

probability = f.model.predict_proba(vector.reshape(1, -1))[0, 1]
print(f"V1 probability : {probability:.6f}")
print(f"threshold      : {f.threshold}")
print(f"verdict        : {'DETECTABLE' if probability >= f.threshold else 'NOT DETECTABLE'}")
