"""Faultiva V1 feature pipeline - builds the 646-feature vector.

SCALING CONVENTION (frozen, 12C-4A) - the two blocks use OPPOSITE conventions:

  cell_fanout   stored RAW      -> the schema's numeric_preprocessing MUST be applied
  k3_features   stored SCALED   -> k3_mean / k3_scale must NOT be applied again

k3_mean and k3_scale are the provenance record of a scaler already applied.
Applying them a second time inflates the graph branch from a p99.99 of 12.6 to
roughly 48,000 and saturates the model: 36% of probabilities collapse onto
exactly 0.0 or 1.0 and fault-polarity sensitivity falls from 100/100 sites to
48/100.  The pipeline still returns confident verdicts, so the defect is silent.

This module applies both conventions correctly.  Use build_vector(); do not
assemble the 646 features by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PKG = Path(__file__).resolve().parent.parent

N_SITE_FEATURES = 14
N_GRAPH_FEATURES = 119
N_TOTAL_FEATURES = 646


class FeaturePipeline:
    """Builds V1's 646-feature input vector with the frozen scaling convention."""

    def __init__(self, package_root: Path | None = None) -> None:
        root = Path(package_root) if package_root else PKG
        self.schema = json.loads(
            (root / "models/hmac_leakage_safe_feature_matrix_schema_11c5e.json")
            .read_text(encoding="utf-8"))

        graph = np.load(root / "data/hmac_golden_netlist_graph_11d1a.npz",
                        allow_pickle=True)
        self.fanout = graph["node_cell_fanout"]
        self.driver_code = graph["node_driver_type_code"]
        self.is_primary_output = graph["node_is_primary_output"]
        self.site_category = graph["node_site_category_code"]

        sgc = np.load(root / "data/hmac_directed_sgc_graph_features_11d1c.npz",
                      allow_pickle=True)
        # ALREADY SCALED - see module docstring
        self.k3 = sgc["k3_features"]

        pre = self.schema["numeric_preprocessing"]
        self.fanout_mean = float(pre["mean_float64"])
        self.fanout_scale = float(pre["scale_float64"])
        self.driver_vocab = self.schema["categorical_vocabulary"]["driver_cell_type"]
        self.category_vocab = self.schema["categorical_vocabulary"]["site_category"]
        self.n_stimulus = int(self.schema["stimulus_feature_count"])

        if self.k3.shape[1] != N_GRAPH_FEATURES:
            raise ValueError(f"graph cache has {self.k3.shape[1]} features, "
                             f"expected {N_GRAPH_FEATURES}")

    @property
    def n_nodes(self) -> int:
        return int(self.k3.shape[0])

    def site_features(self, node: int) -> np.ndarray:
        v = np.zeros(N_SITE_FEATURES, dtype=np.float32)
        v[0] = (float(self.fanout[node]) - self.fanout_mean) / self.fanout_scale
        v[1] = float(self.is_primary_output[node])
        code = int(self.driver_code[node])
        v[2] = 1.0 if "DFF" in self.driver_vocab[code] else 0.0
        v[3 + code] = 1.0
        v[3 + len(self.driver_vocab) + int(self.site_category[node])] = 1.0
        return v

    def graph_features(self, node: int) -> np.ndarray:
        """Return pre-scaled graph features. Do NOT rescale - see docstring."""
        return self.k3[node].astype(np.float32)

    def build_vector(self, node: int, stuck_value: int,
                     key_bits, message_bits) -> np.ndarray:
        """Assemble one 646-feature vector.

        node         node index into the golden netlist graph
        stuck_value  0 for stuck-at-0, 1 for stuck-at-1
        key_bits     256 bits, MSB first (key_bit_255 .. key_bit_0)
        message_bits 256 bits, MSB first (message_bit_255 .. message_bit_0)
        """
        if stuck_value not in (0, 1):
            raise ValueError("stuck_value must be 0 or 1")
        if not 0 <= node < self.n_nodes:
            raise ValueError(f"node {node} out of range [0, {self.n_nodes})")
        key = np.asarray(key_bits, dtype=np.float32).ravel()
        msg = np.asarray(message_bits, dtype=np.float32).ravel()
        if key.size + msg.size != self.n_stimulus:
            raise ValueError(f"expected {self.n_stimulus} stimulus bits, "
                             f"got {key.size} + {msg.size}")
        vector = np.concatenate([
            np.array([stuck_value], dtype=np.float32),
            self.site_features(node),
            key, msg,
            self.graph_features(node),
        ])
        if vector.size != N_TOTAL_FEATURES:
            raise ValueError(f"built {vector.size} features, "
                             f"expected {N_TOTAL_FEATURES}")
        return vector

    def build_batch(self, nodes, stuck_value: int,
                    key_bits, message_bits) -> np.ndarray:
        return np.stack([self.build_vector(int(n), stuck_value, key_bits, message_bits)
                         for n in nodes])
