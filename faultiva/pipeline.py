"""Faultiva inference pipeline: detection, localization, verification.

    observed responses
        |
        v
    [1] DETECTION      V2.2, 0 learned parameters, golden-response comparison
        |
        v
    [2] LOCALIZATION   V2.2, 0 learned parameters, exact behaviour-signature lookup
        |
        v
    [3] VERIFICATION   V1 MLP, 93,185 parameters, threshold 0.4965

Stages 1 and 2 reason from BEHAVIOUR.  Stage 3 reasons from STRUCTURE - V1 uses
zero post-simulation features - so agreement between them is genuine
corroboration from disjoint evidence rather than one model confirming itself.

SCOPE: V1 was trained on opentitan_hmac_sha256 only.  On any other circuit the
verifier returns OUT_OF_SCOPE.  It never guesses.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .feature_pipeline import FeaturePipeline
from .signatures import signature_from_responses

PKG = Path(__file__).resolve().parent.parent
V1_SCOPE_FAMILY = "opentitan_hmac_sha256"


@dataclass
class FaultivaResult:
    circuit: str
    detection: str = ""
    localization: str = ""
    candidate_sites: np.ndarray = field(default_factory=lambda: np.empty(0, int))
    stuck_value: int | None = None
    verification: str = ""
    verification_detail: dict = field(default_factory=dict)
    signature: str = ""

    def summary(self) -> str:
        n = self.candidate_sites.size
        lines = [
            f"[1] DETECTION       {self.detection}",
            f"[2] LOCALIZATION    {self.localization}"
            + (f"  {n} candidate{'s' if n != 1 else ''}" if n else "")
            + (f"  SA{self.stuck_value}" if self.stuck_value is not None else ""),
            f"[3] VERIFICATION    {self.verification}",
        ]
        return "\n".join(lines)


class Faultiva:
    """The shipped hybrid fault detection and localization pipeline."""

    def __init__(self, package_root: Path | None = None) -> None:
        self.root = Path(package_root) if package_root else PKG
        self.features = FeaturePipeline(self.root)

        d = np.load(self.root / "models/faultiva_signature_dictionary.npz",
                    allow_pickle=True)
        self.families = [str(f) for f in d["families"]]
        self._sig = {}
        self._sites = {}
        self._offsets = {}
        for fam in self.families:
            self._sig[fam] = d[f"{fam}__signatures"]
            self._sites[fam] = d[f"{fam}__sites"]
            self._offsets[fam] = d[f"{fam}__offsets"]

        gb = self.root / "data/faultiva_golden_baselines.npz"
        if gb.is_file():
            g = np.load(gb, allow_pickle=True)
            self._golden = {c: g[f"{c}__golden"] for c in self.families
                            if f"{c}__golden" in g}
            self._vectors = {c: int(g[f"{c}__vectors"]) for c in self.families
                             if f"{c}__vectors" in g}
        else:
            self._golden, self._vectors = {}, {}

        self._model = None
        self._threshold = float(json.loads(
            (self.root / "models/hmac_final_diagnostic_model_lock_11d2d.json")
            .read_text(encoding="utf-8"))["threshold"])

    @property
    def model(self):
        if self._model is None:
            import joblib
            self._model = joblib.load(self.root / "models/hmac_hybrid_v1_original.joblib")
        return self._model

    @property
    def threshold(self) -> float:
        return self._threshold

    def signature_count(self, family: str) -> int:
        return int(self._sig[family].size)

    # ---------------- stage 1: detection ----------------
    @staticmethod
    def behaviour_signature(responses) -> str:
        """SHA-256 over the observed response sequence."""
        h = hashlib.sha256()
        for r in responses:
            h.update(str(r).encode())
            h.update(b"|")
        return h.hexdigest()

    def detect(self, observed, golden) -> tuple[str, str]:
        """Compare responses against a golden reference.

        Returns the verdict and, when a fault is present, an *opaque* digest of
        the mismatch pattern. That digest is NOT a catalogue key: use
        :meth:`signature` or :func:`faultiva.signatures.signature_from_responses`
        to obtain a key that can be looked up with :meth:`localize`.
        """
        obs = list(observed)
        gold = list(golden)
        if len(obs) != len(gold):
            raise ValueError(f"observed has {len(obs)} responses, "
                             f"golden has {len(gold)}")
        differing = sum(1 for a, b in zip(obs, gold) if a != b)
        if differing == 0:
            return "NO_FAULT_DETECTED", ""
        return "FAULT_DETECTED", self.behaviour_signature(
            [a != b for a, b in zip(obs, gold)])

    # ---------------- catalogue keys ----------------
    def golden(self, family: str) -> np.ndarray:
        """Fault-free reference responses for a characterized circuit.

        Shape is (vectors, 32) uint8. Raises if the family is not shipped with
        a baseline.
        """
        if family not in self._golden:
            raise KeyError(f"no golden baseline shipped for {family!r}; "
                           f"available: {sorted(self._golden)}")
        return self._golden[family]

    def vectors(self, family: str) -> int:
        """Number of vectors in the frozen plan for this circuit."""
        return self._vectors[family]

    def signature(self, family: str, observed, golden, *,
                  cycle_delta=None, timed_out=None,
                  protocol_error=None) -> str:
        """Compute the catalogue key for captured response bytes.

        ``observed`` and ``golden`` are (vectors, 32) uint8 arrays. This is the
        key :meth:`localize` expects; see ``faultiva.signatures`` for the full
        definition and for when the optional channels matter.
        """
        return signature_from_responses(
            family, observed, golden, cycle_delta=cycle_delta,
            timed_out=timed_out, protocol_error=protocol_error)

    # ---------------- stage 2: localization ----------------
    def localize(self, family: str, signature: str) -> np.ndarray:
        if family not in self._sig:
            raise KeyError(f"unknown circuit family {family!r}; "
                           f"known: {self.families}")
        sig = self._sig[family]
        idx = np.searchsorted(sig, signature)
        if idx >= sig.size or sig[idx] != signature:
            return np.empty(0, dtype=np.int64)
        start = self._offsets[family][idx]
        end = self._offsets[family][idx + 1]
        return self._sites[family][start:end]

    # ---------------- stage 3: verification ----------------
    def verify(self, family: str, nodes, stuck_value: int,
               key_bits, message_bits) -> tuple[str, dict]:
        if family != V1_SCOPE_FAMILY:
            return "OUT_OF_SCOPE", {
                "reason": f"V1 was trained only on {V1_SCOPE_FAMILY}; applying it "
                          f"to {family} would be an unsupported extrapolation"}
        nodes = np.asarray(nodes, dtype=np.int64)
        if nodes.size == 0:
            return "NO_CANDIDATES", {}
        X = self.features.build_batch(nodes, stuck_value, key_bits, message_bits)
        p = self.model.predict_proba(X)[:, 1]
        above = p >= self._threshold
        return ("VERIFIED" if above.any() else "FLAGGED"), {
            "candidates": int(p.size),
            "max_probability": float(p.max()),
            "mean_probability": float(p.mean()),
            "above_threshold": int(above.sum()),
            "threshold": self._threshold,
        }

    # ---------------- full pipeline ----------------
    def analyse(self, family: str, observed, golden, *,
                stuck_value: int | None = None,
                key_bits=None, message_bits=None,
                cycle_delta=None, timed_out=None,
                protocol_error=None) -> FaultivaResult:
        """Run the full pipeline.

        ``observed`` and ``golden`` may be a sequence of response records, or a
        (vectors, 32) uint8 array of response bytes. Byte arrays enable
        localization; plain sequences support detection only, because the
        catalogue is keyed on measured response bytes.
        """
        result = FaultivaResult(circuit=family)
        result.detection, mismatch_digest = self.detect(observed, golden)
        if result.detection == "NO_FAULT_DETECTED":
            result.localization = "NOT_APPLICABLE"
            result.verification = "NOT_APPLICABLE"
            return result

        obs_arr = np.asarray(observed)
        gold_arr = np.asarray(golden)
        byte_shaped = (obs_arr.ndim == 2 and obs_arr.shape == gold_arr.shape
                       and obs_arr.shape[1] == 32)
        if byte_shaped:
            signature = self.signature(
                family, obs_arr, gold_arr, cycle_delta=cycle_delta,
                timed_out=timed_out, protocol_error=protocol_error)
        else:
            result.signature = mismatch_digest
            result.localization = "REQUIRES_RESPONSE_BYTES"
            result.verification = "NOT_APPLICABLE"
            return result

        result.signature = signature
        sites = self.localize(family, signature)
        result.candidate_sites = sites
        result.stuck_value = stuck_value
        if sites.size == 0:
            result.localization = "UNKNOWN_SIGNATURE"
        elif sites.size == 1:
            result.localization = "EXACT"
        else:
            result.localization = "AMBIGUOUS"

        if sites.size and stuck_value is not None and key_bits is not None:
            result.verification, result.verification_detail = self.verify(
                family, sites, stuck_value, key_bits, message_bits)
        else:
            result.verification = "NOT_REQUESTED"
        return result
