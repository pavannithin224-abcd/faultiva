"""Configuration schema for third-party circuit characterization.

A researcher describes their design in YAML; this module validates it and
refuses anything the generated testbench cannot honestly drive.

SUPPORTED INTERFACE: `simple_handshake` only.

    reset -> drive inputs -> pulse start -> wait for done -> read output

That is the pattern used by secworks_aes, secworks_sha256 and many crypto
cores.  It is NOT universal.  Designs needing FIFO adapters, bus protocols
(AXI/Wishbone), multi-beat streaming or instruction memories are rejected
with an explicit message rather than silently mis-driven.

NOTE ON SCOPE HONESTY: opentitan_hmac_sha256 - one of the four bundled
circuits - required a bespoke SystemVerilog FIFO adapter and would itself be
REJECTED by this schema.  The tool handles the common case, not every case.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_PROTOCOLS = ("simple_handshake",)

# a Verilog identifier
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# circuit ids become directory names and JSON keys
CIRCUIT_ID = re.compile(r"^[a-z0-9][a-z0-9_]{2,62}$")

MAX_VECTORS = 4096
MAX_TOTAL_INPUT_BITS = 8192
MAX_OUTPUT_BITS = 4096
MAX_CYCLE_BUDGET = 1_000_000

RESERVED_PORTS = ("fi_enable_i", "fi_site_onehot_i", "fi_stuck_value_i")


class ConfigError(ValueError):
    """Raised with an actionable message when a config cannot be honoured."""


@dataclass(frozen=True)
class Port:
    name: str
    width: int

    @property
    def bytes_needed(self) -> int:
        return (self.width + 7) // 8


@dataclass(frozen=True)
class Signal:
    name: str
    active_low: bool = False
    pulse: bool = False


@dataclass(frozen=True)
class Constant:
    """An input pin held at a fixed value for the whole campaign."""
    name: str
    width: int
    value: int


@dataclass
class CircuitConfig:
    """A validated description of a third-party circuit."""

    circuit: str
    top: str
    rtl: list[Path]
    clock: str
    reset: Signal
    protocol: str
    start: Signal
    done: Signal
    inputs: list[Port]
    output: Port
    vectors: int
    cycle_budget: int
    constants: list[Constant] = field(default_factory=list)
    seed: str = ""
    synthesis_script: str = ""
    abc_gates: str = "AND,OR,XOR,MUX"
    opt_clean: bool = True
    include_dirs: list[Path] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    # ---------------------------------------------------------------- derived
    @property
    def input_bits(self) -> int:
        return sum(p.width for p in self.inputs)

    @property
    def response_bytes(self) -> int:
        return (self.output.width + 7) // 8

    def port_names(self) -> list[str]:
        return ([self.clock, self.reset.name, self.start.name, self.done.name]
                + [p.name for p in self.inputs]
                + [c.name for c in self.constants] + [self.output.name])


# --------------------------------------------------------------------- helpers
def _req(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required field '{key}' in {where}")
    return mapping[key]


def _ident(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENT.match(value):
        raise ConfigError(
            f"{where}: '{value}' is not a valid Verilog identifier")
    return value


def _signal(value: Any, where: str, allow_pulse: bool) -> Signal:
    """Accept either a bare name or a mapping with modifiers."""
    if isinstance(value, str):
        return Signal(_ident(value, where))
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a name or a mapping, got {type(value).__name__}")
    name = _ident(_req(value, "name", where), f"{where}.name")
    active_low = bool(value.get("active_low", False))
    pulse = bool(value.get("pulse", False))
    if pulse and not allow_pulse:
        raise ConfigError(f"{where}: 'pulse' is only meaningful for 'start'")
    unknown = set(value) - {"name", "active_low", "pulse"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    return Signal(name, active_low=active_low, pulse=pulse)


def _width(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: width must be an integer, got {value!r}")
    if value < 1:
        raise ConfigError(f"{where}: width must be >= 1, got {value}")
    return value


def _constant(value: Any, where: str) -> "Constant":
    """A held input: {name, width, value}."""
    if not isinstance(value, dict):
        raise ConfigError(
            f"{where}: expected a mapping with 'name', 'width' and 'value'")
    unknown = set(value) - {"name", "width", "value"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    name = _ident(_req(value, "name", where), f"{where}.name")
    width = _width(_req(value, "width", where), f"{where}.width")
    raw = _req(value, "value", where)
    if isinstance(raw, bool):
        held = 1 if raw else 0
    elif isinstance(raw, int):
        held = raw
    elif isinstance(raw, str):
        try:
            held = int(raw, 0)
        except ValueError as exc:
            raise ConfigError(
                f"{where}.value: '{raw}' is not an integer literal") from exc
    else:
        raise ConfigError(f"{where}.value: expected an integer, got {raw!r}")
    if held < 0:
        raise ConfigError(f"{where}.value: must be >= 0, got {held}")
    if held >= (1 << width):
        raise ConfigError(
            f"{where}.value: {held} does not fit in {width} bit(s)")
    return Constant(name, width, held)


def _port(value: Any, where: str) -> Port:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a mapping with 'name' and 'width'")
    unknown = set(value) - {"name", "width"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    return Port(_ident(_req(value, "name", where), f"{where}.name"),
                _width(_req(value, "width", where), f"{where}.width"))


# ----------------------------------------------------------------------- parse
def parse_config(raw: dict[str, Any], base_dir: Path) -> CircuitConfig:
    """Validate a parsed YAML mapping into a CircuitConfig.

    Every rejection carries a message explaining what to change.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    known = {"circuit", "top", "rtl", "clock", "reset", "protocol", "start",
             "done", "inputs", "output", "vectors", "cycle_budget", "seed",
             "include_dirs", "defines", "notes", "constants",
             "synthesis_script", "abc_gates", "opt_clean"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown top-level key(s) {sorted(unknown)}; "
                          f"supported keys are {sorted(known)}")

    # -- identity -----------------------------------------------------------
    circuit = _req(raw, "circuit", "config")
    if not isinstance(circuit, str) or not CIRCUIT_ID.match(circuit):
        raise ConfigError(
            f"circuit: '{circuit}' must be 3-63 chars of [a-z0-9_] and start "
            "with a letter or digit (it becomes a directory name)")
    top = _ident(_req(raw, "top", "config"), "top")

    # -- protocol gate ------------------------------------------------------
    protocol = raw.get("protocol", "simple_handshake")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ConfigError(
            f"protocol '{protocol}' is not supported.  This tool generates "
            f"testbenches for {SUPPORTED_PROTOCOLS[0]} only: reset, drive "
            "inputs, pulse start, wait for done, read output.  Designs with "
            "FIFO interfaces, bus protocols (AXI/Wishbone), multi-beat "
            "streaming or instruction memories need a hand-written "
            "testbench - see docs/CHARACTERIZATION.md.")

    # -- sources ------------------------------------------------------------
    rtl_raw = _req(raw, "rtl", "config")
    if isinstance(rtl_raw, str):
        rtl_raw = [rtl_raw]
    if not isinstance(rtl_raw, list) or not rtl_raw:
        raise ConfigError("rtl: expected a non-empty list of source files")
    rtl: list[Path] = []
    for item in rtl_raw:
        if not isinstance(item, str):
            raise ConfigError(f"rtl: entries must be paths, got {item!r}")
        p = (base_dir / item).resolve() if not Path(item).is_absolute() else Path(item)
        if not p.is_file():
            raise ConfigError(f"rtl: file not found: {p}")
        rtl.append(p)

    include_dirs: list[Path] = []
    for item in raw.get("include_dirs", []) or []:
        p = (base_dir / item).resolve() if not Path(item).is_absolute() else Path(item)
        if not p.is_dir():
            raise ConfigError(f"include_dirs: directory not found: {p}")
        include_dirs.append(p)

    defines = raw.get("defines", {}) or {}
    if not isinstance(defines, dict):
        raise ConfigError("defines: expected a mapping of NAME: value")
    defines = {str(k): str(v) for k, v in defines.items()}

    # -- signals ------------------------------------------------------------
    clock = _ident(_req(raw, "clock", "config"), "clock")
    reset = _signal(_req(raw, "reset", "config"), "reset", allow_pulse=False)
    start = _signal(_req(raw, "start", "config"), "start", allow_pulse=True)
    done = _signal(_req(raw, "done", "config"), "done", allow_pulse=False)

    # -- data ports ---------------------------------------------------------
    inputs_raw = _req(raw, "inputs", "config")
    if not isinstance(inputs_raw, list) or not inputs_raw:
        raise ConfigError("inputs: expected a non-empty list of {name, width}")
    inputs = [_port(v, f"inputs[{i}]") for i, v in enumerate(inputs_raw)]
    output = _port(_req(raw, "output", "config"), "output")

    constants_raw = raw.get("constants", []) or []
    if not isinstance(constants_raw, list):
        raise ConfigError("constants: expected a list of {name, width, value}")
    constants = [_constant(v, f"constants[{i}]")
                 for i, v in enumerate(constants_raw)]

    # -- numeric budgets ---------------------------------------------------
    vectors = _req(raw, "vectors", "config")
    if isinstance(vectors, bool) or not isinstance(vectors, int) or not 1 <= vectors <= MAX_VECTORS:
        raise ConfigError(f"vectors: must be an integer 1..{MAX_VECTORS}, got {vectors!r}")
    budget = raw.get("cycle_budget", 1000)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= MAX_CYCLE_BUDGET:
        raise ConfigError(
            f"cycle_budget: must be an integer 1..{MAX_CYCLE_BUDGET}, got {budget!r}")

    cfg = CircuitConfig(
        circuit=circuit, top=top, rtl=rtl, clock=clock, reset=reset,
        protocol=protocol, start=start, done=done, inputs=inputs,
        output=output, vectors=vectors, cycle_budget=budget,
        constants=constants,
        seed=str(raw.get("seed", "") or ""),
        synthesis_script=str(raw.get("synthesis_script", "") or ""),
        abc_gates=str(raw.get("abc_gates", "AND,OR,XOR,MUX")),
        opt_clean=bool(raw.get("opt_clean", True)),
        include_dirs=include_dirs,
        defines=defines, notes=str(raw.get("notes", "") or ""),
    )
    _cross_check(cfg)
    return cfg


def _cross_check(cfg: CircuitConfig) -> None:
    """Checks that need the whole config assembled."""
    names = cfg.port_names()
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"port name(s) used more than once: {dupes}")

    clash = sorted(set(names) & set(RESERVED_PORTS))
    if clash:
        raise ConfigError(
            f"port name(s) {clash} collide with the fault-injection ports "
            f"{list(RESERVED_PORTS)} added during instrumentation; rename them "
            "in your design")

    if cfg.input_bits > MAX_TOTAL_INPUT_BITS:
        raise ConfigError(
            f"total input width {cfg.input_bits} exceeds the supported maximum "
            f"{MAX_TOTAL_INPUT_BITS} bits")
    if cfg.output.width > MAX_OUTPUT_BITS:
        raise ConfigError(
            f"output width {cfg.output.width} exceeds the supported maximum "
            f"{MAX_OUTPUT_BITS} bits")

    if cfg.reset.name == cfg.clock:
        raise ConfigError("reset and clock cannot be the same signal")

    if cfg.synthesis_script:
        for token in ("{sources}", "{top}", "{json_out}"):
            if token not in cfg.synthesis_script:
                raise ConfigError(
                    f"synthesis_script must contain {token} - the runner "
                    "substitutes the source list, top module and output path")
    if not cfg.abc_gates.strip():
        raise ConfigError("abc_gates cannot be empty")


def load_config(path: Path) -> CircuitConfig:
    """Read and validate a YAML config file."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ConfigError(
            "PyYAML is required to read config files: pip install pyyaml") from exc
    raw = yaml.safe_load(text)
    return parse_config(raw, path.parent)
