"""Faultiva characterization: bring your own circuit.

    RTL + config.yaml  ->  synthesize  ->  enumerate sites  ->  instrument
                       ->  simulate every fault  ->  signature catalogue

After characterization the circuit appears in the Faultiva dashboard beside
the four bundled ones and is diagnosed the same way.

GOLDEN RESPONSE: for third-party circuits the fault-free reference comes from
simulating the user's own netlist with fault injection disabled, NOT from an
independent software model of the algorithm.  Localization only needs
differences from fault-free behaviour, so this is sufficient - but it cannot
detect a functional bug in the design itself.  The four bundled circuits were
characterized against independent oracles (AES, SHA-256 and a RISC-V
emulator) and therefore do carry that extra cross-check.

SCOPE: `simple_handshake` interfaces only.  See config.py.
"""
from __future__ import annotations

from .config import (CircuitConfig, ConfigError, Port, Signal,
                     SUPPORTED_PROTOCOLS, load_config, parse_config)
from .netlist import (NetlistError, canonical_json, enumerate_sites,
                      instrument_netlist, load_netlist, maximum_net_bit,
                      site_summary)

__all__ = [
    "CircuitConfig", "ConfigError", "Port", "Signal", "SUPPORTED_PROTOCOLS",
    "load_config", "parse_config",
    "NetlistError", "canonical_json", "enumerate_sites", "instrument_netlist",
    "load_netlist", "maximum_net_bit", "site_summary",
]
