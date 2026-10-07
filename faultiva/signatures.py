"""Faultiva behaviour signatures.

A behaviour signature is the key the localization dictionary is built on. It is
a SHA-256 over four measured channels of a circuit's response to the frozen
vector plan:

    family_id      identifies which catalogue to look in
    response_xor   observed XOR golden, per response byte, per vector
    cycle_delta    completion-cycle difference from the fault-free baseline
    timed_out      per-vector timeout flag
    protocol_error per-vector protocol-violation flag

This is the definition used by the fault campaign that produced the shipped
dictionary (stage 12C-1I, reused unchanged by 12C-1L and 12C-1O). Recomputing
it from campaign response data reproduces 1,577 of 1,577 observable faults'
catalogued keys exactly.

Why the shape matters
---------------------
``response_xor`` must be (vectors, response_bytes) uint8 and the three flag
channels must be
(vectors,) with dtypes int32/uint8/uint8. The digest is taken over raw bytes, so
a wrong dtype or a transposed array silently produces a different key that will
never match the catalogue.

Faults that are not observable at the circuit output have no catalogued
signature at all, by construction: the campaign only recorded signatures for
faults that changed observable behaviour.
"""
from __future__ import annotations

import hashlib

import numpy as np

__all__ = ["signature_digest", "signature_from_responses", "N_RESPONSE_BYTES"]

#: response width in bytes for the four BUNDLED circuits, fixed by the frozen
#: V2.2 vector plan.  It is not a property of the signature scheme: a
#: user-characterized circuit may have any response width, and
#: signature_digest() hashes whatever it is given.
N_RESPONSE_BYTES = 32


def signature_digest(family_id: str,
                     response_xor: np.ndarray,
                     cycle_delta: np.ndarray,
                     timed_out: np.ndarray,
                     protocol_error: np.ndarray) -> str:
    """Return the catalogue key for one fault's measured behaviour.

    This is the canonical definition. Do not reorder the updates, change the
    dtypes, or omit the trailing NUL after ``family_id`` — each of those
    produces a different digest.
    """
    digest = hashlib.sha256()
    digest.update(family_id.encode() + b"\0")
    digest.update(np.ascontiguousarray(response_xor).tobytes())
    digest.update(np.ascontiguousarray(cycle_delta.astype("<i4", copy=False)).tobytes())
    digest.update(np.ascontiguousarray(timed_out).tobytes())
    digest.update(np.ascontiguousarray(protocol_error).tobytes())
    return digest.hexdigest()


def signature_from_responses(family_id: str,
                             observed: np.ndarray,
                             golden: np.ndarray,
                             *,
                             cycle_delta: np.ndarray | None = None,
                             timed_out: np.ndarray | None = None,
                             protocol_error: np.ndarray | None = None) -> str:
    """Build a catalogue key from captured response bytes.

    ``observed`` and ``golden`` are (vectors, response_bytes) uint8 arrays.
    Response width is a property of the circuit - 32 bytes for the four bundled
    circuits, whatever the design produces for a characterized one - so the two
    arrays need only agree with each other. The three optional channels default to zeros, which is correct for a
    circuit that produced wrong data but completed normally — the common case
    for a functional stuck-at fault.

    Supply them when your capture harness recorded them. Leaving them at zero
    when the real circuit timed out will produce a key that does not match the
    catalogue, and localization will correctly report UNKNOWN_SIGNATURE rather
    than guess.
    """
    observed = np.ascontiguousarray(observed, dtype=np.uint8)
    golden = np.ascontiguousarray(golden, dtype=np.uint8)
    if observed.shape != golden.shape:
        raise ValueError(f"observed {observed.shape} and golden {golden.shape} "
                         f"must have the same shape")
    if observed.ndim != 2 or observed.shape[1] < 1:
        raise ValueError(f"expected a (vectors, response_bytes) array, "
                         f"got {observed.shape}")

    vectors = observed.shape[0]
    response_xor = np.bitwise_xor(observed, golden)
    if cycle_delta is None:
        cycle_delta = np.zeros(vectors, dtype=np.int32)
    if timed_out is None:
        timed_out = np.zeros(vectors, dtype=np.uint8)
    if protocol_error is None:
        protocol_error = np.zeros(vectors, dtype=np.uint8)

    for name, arr, n in (("cycle_delta", cycle_delta, vectors),
                         ("timed_out", timed_out, vectors),
                         ("protocol_error", protocol_error, vectors)):
        if np.asarray(arr).shape != (n,):
            raise ValueError(f"{name} must have shape ({n},), got "
                             f"{np.asarray(arr).shape}")

    return signature_digest(family_id, response_xor,
                            np.asarray(cycle_delta, dtype=np.int32),
                            np.asarray(timed_out, dtype=np.uint8),
                            np.asarray(protocol_error, dtype=np.uint8))
