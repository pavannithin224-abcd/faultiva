"""Deterministic stimulus generation for third-party circuit characterization.

Vectors are derived by hashing, never randomly sampled, so the same config
always produces the same test set on any machine:

    bits = SHA256( circuit_id \\0 port_name \\0 vector_index \\0 seed )

expanded by counter mode when a port needs more than 256 bits.  This mirrors
the V2.2 campaign's rule - SHA256(family_id || vector_index || contract_id) -
so reproducibility does not depend on a stored vector file.

Written as Verilog $readmemh files, one per input port, one line per vector,
most-significant hex digit first.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import CircuitConfig, Port


def port_bits(circuit: str, port: Port, index: int, seed: str) -> int:
    """Deterministic integer of exactly port.width bits."""
    need = (port.width + 7) // 8
    out = b""
    counter = 0
    while len(out) < need:
        material = f"{circuit}\0{port.name}\0{index}\0{seed}\0{counter}".encode()
        out += hashlib.sha256(material).digest()
        counter += 1
    value = int.from_bytes(out[:need], "big")
    return value & ((1 << port.width) - 1)


def vector_table(cfg: CircuitConfig) -> dict[str, list[int]]:
    """Every input port's stimulus for every vector."""
    return {
        port.name: [port_bits(cfg.circuit, port, i, cfg.seed)
                    for i in range(cfg.vectors)]
        for port in cfg.inputs
    }


def write_mem_files(cfg: CircuitConfig, memory_dir: Path) -> dict[str, Path]:
    """Write one $readmemh file per input port.  Returns {port: path}."""
    memory_dir = Path(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)
    table = vector_table(cfg)
    written: dict[str, Path] = {}
    for port in cfg.inputs:
        digits = (port.width + 3) // 4
        path = memory_dir / f"{port.name}.mem"
        lines = [f"{value:0{digits}x}" for value in table[port.name]]
        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        written[port.name] = path
    return written


def stimulus_digest(cfg: CircuitConfig) -> str:
    """One hash over the whole stimulus set, for the manifest."""
    h = hashlib.sha256()
    h.update(f"{cfg.circuit}\0{cfg.seed}\0{cfg.vectors}\n".encode())
    table = vector_table(cfg)
    for port in cfg.inputs:
        h.update(f"{port.name}\0{port.width}\n".encode())
        for value in table[port.name]:
            h.update(f"{value:x}\n".encode())
    return h.hexdigest()
