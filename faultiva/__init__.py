"""Faultiva - hybrid fault detection and localization for VLSI circuits.

    from faultiva import Faultiva
    f = Faultiva()
    result = f.analyse("opentitan_hmac_sha256", observed, golden)
    print(result.summary())

Read docs/HONEST_LIMITS.md before drawing conclusions from any output.
"""
from .pipeline import Faultiva, FaultivaResult, V1_SCOPE_FAMILY
from .feature_pipeline import FeaturePipeline
from .signatures import signature_digest, signature_from_responses

__version__ = "1.0.0"
__all__ = ["Faultiva", "FaultivaResult", "FeaturePipeline", "V1_SCOPE_FAMILY",
           "signature_digest", "signature_from_responses"]
