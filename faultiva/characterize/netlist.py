"""Circuit-agnostic netlist operations: site enumeration and fault instrumentation.

Ported from stage_12c1i_adapter_pilot_execution.py with the per-family
hardcoding removed.  The original indexed a module-level TOPS dict and capped
site count at a fixed SITES_PER_FAMILY; here the top module is a parameter and
all eligible sites are enumerated.

VALIDATED: these functions must reproduce the frozen V2.2 site tables and
instrumented netlists byte-for-byte.  See characterize/selftest.py.

WHAT A SITE IS
    One distinct driven net bit on a cell output port.  Bits 0 and 1 are the
    Yosys constants (0/1) and are skipped; a net bit already claimed by an
    earlier cell is skipped so each physical net is one site, not several.

    Cells with no output connections - notably Yosys `$scopeinfo` hierarchy
    annotations - yield no sites.  That property is why the V2.2 campaign's
    frozen site budget overshot reality by 7 sites and needed amending.

HOW INSTRUMENTATION WORKS
    For every site, the cell's output bit is rerouted through a MUX:

        cell ---> raw_bit ---+
                             +--> MUX --> original net
        fi_stuck_value_i ----+     ^
                                   |
        fi_enable_i ---+           |
                       +-- AND ----+
        onehot[rank] --+

    So one build supports every fault: enable + select a site + choose the
    stuck value, all at runtime.  Nothing is recompiled per fault.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

FI_ENABLE = "fi_enable_i"
FI_ONEHOT = "fi_site_onehot_i"
FI_STUCK = "fi_stuck_value_i"


class NetlistError(ValueError):
    """Raised when a netlist cannot be enumerated or instrumented."""


def canonical_json(value: Any) -> bytes:
    """Byte-stable JSON, matching the V2.2 campaign's encoder exactly."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def load_netlist(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def integer_bits(value: Any) -> list[int]:
    """Net bits that are real nets, not Yosys constants."""
    if not isinstance(value, list):
        return []
    return [b for b in value if isinstance(b, int)]


def maximum_net_bit(module: dict[str, Any]) -> int:
    """Highest net bit id used anywhere in the module."""
    highest = 1
    for collection in ("ports", "netnames"):
        for entry in module.get(collection, {}).values():
            for bit in integer_bits(entry.get("bits", [])):
                highest = max(highest, bit)
    for cell in module.get("cells", {}).values():
        for connection in cell.get("connections", {}).values():
            for bit in integer_bits(connection):
                highest = max(highest, bit)
    return highest


def enumerate_sites(circuit_id: str, netlist: Path | dict[str, Any],
                    top: str, *, limit: int | None = None,
                    family_number: int = 0) -> list[dict[str, Any]]:
    """Enumerate every eligible fault site in `top`.

    `circuit_id` and `family_number` only affect the opaque site id, which
    exists so a site can be referenced without leaking its cell name into
    model-facing data.
    """
    design = netlist if isinstance(netlist, dict) else load_netlist(Path(netlist))
    modules = design.get("modules", {})
    if top not in modules:
        raise NetlistError(
            f"top module '{top}' not in netlist; available: {sorted(modules)[:12]}")
    module = modules[top]

    sites: list[dict[str, Any]] = []
    used_bits: set[int] = set()
    for cell_name in sorted(module.get("cells", {})):
        cell = module["cells"][cell_name]
        directions = cell.get("port_directions", {})
        for port_name in sorted(cell.get("connections", {})):
            if directions.get(port_name) != "output":
                continue
            for bit_index, bit in enumerate(cell["connections"][port_name]):
                if not isinstance(bit, int) or bit < 2 or bit in used_bits:
                    continue
                used_bits.add(bit)
                rank = len(sites)
                identity = f"{circuit_id}\0{cell_name}\0{port_name}\0{bit_index}\0{bit}".encode()
                sites.append({
                    "site_rank": rank,
                    "opaque_site_id":
                        f"S{family_number}-{rank:04d}-{hashlib.sha256(identity).hexdigest()[:12]}",
                    "cell_name": cell_name,
                    "cell_type": str(cell.get("type", "")),
                    "output_port": port_name,
                    "output_bit_index": bit_index,
                    "net_bit_id": bit,
                })
                if limit is not None and len(sites) == limit:
                    return sites
    if not sites:
        raise NetlistError(
            f"{circuit_id}: no eligible fault sites found in '{top}'.  Every "
            "cell lacks a driven output bit - check that synthesis produced a "
            "flattened gate-level netlist.")
    if limit is not None and len(sites) < limit:
        raise NetlistError(
            f"{circuit_id}: only {len(sites)} eligible sites, {limit} required")
    return sites


def instrument_netlist(circuit_id: str, netlist: Path | dict[str, Any],
                       top: str, sites: list[dict[str, Any]]) -> bytes:
    """Insert a runtime-selectable stuck-at MUX at every site.

    Returns canonical JSON bytes, so repeated runs are byte-identical.
    """
    source = netlist if isinstance(netlist, dict) else load_netlist(Path(netlist))
    design = copy.deepcopy(source)
    modules = design.get("modules", {})
    if top not in modules:
        raise NetlistError(f"top module '{top}' not in netlist")
    module = modules[top]

    existing = module.get("ports", {})
    collide = [n for n in (FI_ENABLE, FI_ONEHOT, FI_STUCK) if n in existing]
    if collide:
        raise NetlistError(
            f"{circuit_id}: port name(s) {collide} already exist in '{top}'; "
            "the fault-injection ports cannot be added - rename them in your design")
    if not sites:
        raise NetlistError(f"{circuit_id}: no sites to instrument")

    maximum = maximum_net_bit(module)
    enable_bit = maximum + 1
    stuck_bit = maximum + 2
    onehot_bits = list(range(maximum + 3, maximum + 3 + len(sites)))
    next_bit = maximum + 3 + len(sites)

    ports = module.setdefault("ports", {})
    ports[FI_ENABLE] = {"direction": "input", "bits": [enable_bit]}
    ports[FI_STUCK] = {"direction": "input", "bits": [stuck_bit]}
    ports[FI_ONEHOT] = {"direction": "input", "bits": onehot_bits}

    netnames = module.setdefault("netnames", {})
    netnames[FI_ENABLE] = {"hide_name": 0, "bits": [enable_bit], "attributes": {}}
    netnames[FI_STUCK] = {"hide_name": 0, "bits": [stuck_bit], "attributes": {}}
    netnames[FI_ONEHOT] = {"hide_name": 0, "bits": onehot_bits, "attributes": {}}

    cells = module.setdefault("cells", {})
    for site in sites:
        rank = int(site["site_rank"])
        cell = cells[site["cell_name"]]
        connection = cell["connections"][site["output_port"]]
        old_bit = connection[int(site["output_bit_index"])]
        if old_bit != int(site["net_bit_id"]):
            raise NetlistError(
                f"{circuit_id}: site {rank} does not replay - netlist changed "
                "between enumeration and instrumentation")
        raw_bit, gate_bit = next_bit, next_bit + 1
        next_bit += 2
        connection[int(site["output_bit_index"])] = raw_bit
        cells[f"$v22fi_and${rank}"] = {
            "hide_name": 1, "type": "$_AND_", "parameters": {}, "attributes": {},
            "port_directions": {"A": "input", "B": "input", "Y": "output"},
            "connections": {"A": [enable_bit], "B": [onehot_bits[rank]], "Y": [gate_bit]},
        }
        cells[f"$v22fi_mux${rank}"] = {
            "hide_name": 1, "type": "$_MUX_", "parameters": {}, "attributes": {},
            "port_directions": {"A": "input", "B": "input", "S": "input", "Y": "output"},
            "connections": {"A": [raw_bit], "B": [stuck_bit], "S": [gate_bit], "Y": [old_bit]},
        }
    return canonical_json(design)


def site_summary(sites: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts a user can sanity-check against their own expectations."""
    types: dict[str, int] = {}
    for site in sites:
        types[site["cell_type"]] = types.get(site["cell_type"], 0) + 1
    sequential = sum(n for t, n in types.items() if "DFF" in t.upper() or "LATCH" in t.upper())
    return {
        "sites": len(sites),
        "faults": len(sites) * 2,
        "distinct_cell_types": len(types),
        "sequential_sites": sequential,
        "combinational_sites": len(sites) - sequential,
        "cell_types": dict(sorted(types.items(), key=lambda kv: -kv[1])),
    }
