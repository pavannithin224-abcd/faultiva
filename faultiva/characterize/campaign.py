"""Run a characterization campaign and write a user circuit catalogue.

PHASES
    1  synthesize        RTL -> generic gate netlist (Yosys)
    2  enumerate         every eligible fault site
    3  instrument        one MUX per site, runtime-selectable
    4  build             Verilator binary, once
    5  baseline          fault-free sweep -> the golden reference
    6  campaign          every site x {SA0, SA1}, batched and parallel
    7  catalogue         behaviour signature -> candidate sites

Checkpointed after every batch, so an interrupted run resumes without
re-simulating.  A partial batch is discarded and redone whole rather than
appended to, so a half-written batch can never enter the catalogue.

SIGNATURE: computed by faultiva.signatures.signature_digest over the same four
channels the bundled circuits use - response_xor, cycle_delta, timed_out,
protocol_error - so a user circuit is diagnosed by exactly the same code path.

GOLDEN: phase 5 simulates the user's own netlist with injection disabled.
There is no independent software oracle, so a functional bug in the design
would be baked into the reference.  Localization only needs differences from
fault-free behaviour, so this is sufficient for diagnosis - but it is a real
difference from the bundled circuits and is recorded in the manifest.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from ..signatures import signature_digest
from .config import CircuitConfig
from .netlist import enumerate_sites, instrument_netlist, site_summary
from .stimulus import stimulus_digest, write_mem_files
from .testbench import expected_rows, generate_testbench

BATCH_SITES = 64
BUILD_TIMEOUT = 7200
BATCH_TIMEOUT = 3600
RESPONSE_BITS = 512


class CampaignError(RuntimeError):
    """Raised when a phase fails in a way the user must act on."""


# ------------------------------------------------------------------ utilities
def _tool(name: str, override: str | None = None) -> str:
    """Locate yosys/verilator: explicit override, env var, then PATH."""
    if override:
        return override
    env = os.environ.get(f"FAULTIVA_{name.upper()}")
    if env:
        return env
    found = shutil.which(name)
    if not found:
        raise CampaignError(
            f"{name} not found on PATH.  Install it, or set "
            f"FAULTIVA_{name.upper()} to its full path.")
    return found


def _run(command: list[str], log: Path, timeout: int) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", errors="replace") as handle:
        handle.write("$ " + " ".join(command) + "\n\n")
        handle.flush()
        proc = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                              timeout=timeout)
    return proc.returncode


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _hex_to_bytes(text: str, width: int) -> np.ndarray:
    """Right-most `width` bytes of a fixed-width hex field, MSB first."""
    raw = bytes.fromhex(text.strip().rjust(RESPONSE_BITS // 4, "0"))
    return np.frombuffer(raw[-width:], dtype=np.uint8)


# -------------------------------------------------------------------- phases
def synthesize(cfg: CircuitConfig, work: Path, yosys: str) -> Path:
    netlist = work / f"{cfg.circuit}_generic.json"
    sources = " ".join(f"-sv {p}" if p.suffix in (".sv", ".svh") else str(p)
                       for p in cfg.rtl)
    incs = "".join(f" -I{d}" for d in cfg.include_dirs)
    defs = "".join(f" -D{k}={v}" for k, v in cfg.defines.items())
    if cfg.synthesis_script:
        # the user supplies the whole flow; we only substitute the three
        # values we own
        script = (cfg.synthesis_script
                  .replace("{sources}", f"{incs}{defs} {sources}".strip())
                  .replace("{top}", cfg.top)
                  .replace("{json_out}", str(netlist)))
    else:
        tail = "opt_clean; " if cfg.opt_clean else ""
        script = (
            f"read_verilog{incs}{defs} {sources}; "
            f"hierarchy -check -top {cfg.top}; "
            "proc; opt; memory; opt; techmap; opt; "
            f"abc -g {cfg.abc_gates}; {tail}"
            f"write_json {netlist}"
        )
    (work / "logs").mkdir(parents=True, exist_ok=True)
    (work / "logs/yosys_script.txt").write_text(script + "\n", encoding="utf-8")
    code = _run([yosys, "-q", "-p", script], work / "logs/yosys_synth.log",
                BUILD_TIMEOUT)
    if code != 0 or not netlist.is_file():
        raise CampaignError(
            f"synthesis failed (exit {code}).  See {work/'logs/yosys_synth.log'}.  "
            "Common causes: a module the file list does not include, an "
            "unsupported SystemVerilog construct, or a wrong top module name.")
    return netlist


def build_simulator(cfg: CircuitConfig, work: Path, instrumented_v: Path,
                    testbench: Path, verilator: str) -> Path:
    obj = work / "obj_dir"
    top = f"tb_faultiva_{cfg.circuit}"
    code = _run([verilator, "--binary", "--timing", "-Wno-fatal", "-Wno-WIDTH",
                 "--Mdir", str(obj), "--top-module", top, "-o", "sim",
                 str(instrumented_v), str(testbench)],
                work / "logs/verilator_build.log", BUILD_TIMEOUT)
    binary = obj / "sim"
    if code != 0 or not binary.is_file():
        raise CampaignError(
            f"simulator build failed (exit {code}).  See "
            f"{work/'logs/verilator_build.log'}.")
    return binary


def run_batch(binary: Path, out_dir: Path, batch_id: int, site_start: int,
              site_count: int, expect: int) -> list[dict[str, str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"batch_{batch_id:05d}.csv"
    log_path = out_dir / f"batch_{batch_id:05d}.log"
    code = _run([str(binary), "+MODE=CAMPAIGN", f"+CSV={csv_path}",
                 f"+BATCH_ID={batch_id}", f"+SITE_START={site_start}",
                 f"+SITE_COUNT={site_count}"], log_path, BATCH_TIMEOUT)
    if code != 0:
        raise CampaignError(f"batch {batch_id} failed (exit {code}); see {log_path}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "FAULTIVA_CAMPAIGN_RESULT=PASS" not in text:
        raise CampaignError(f"batch {batch_id} did not report PASS; see {log_path}")
    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expect:
        raise CampaignError(
            f"batch {batch_id} produced {len(rows)} rows, expected {expect} "
            "- the simulator did not emit the authorized transaction count")
    return rows


# ---------------------------------------------------------------- signatures
def signatures_from_rows(cfg: CircuitConfig, rows: list[dict[str, str]],
                         golden: np.ndarray) -> dict[tuple[int, int], str]:
    """One signature per (site, stuck) from a batch's ENABLED rows."""
    width = cfg.response_bytes
    buckets: dict[tuple[int, int], dict[int, dict[str, Any]]] = {}
    base_cycles = {}
    for row in rows:
        if row["run_type"] == "BASELINE":
            base_cycles[int(row["vector"])] = int(row["cycles"])
    for row in rows:
        if row["run_type"] != "ENABLED":
            continue
        key = (int(row["site"]), int(row["stuck"]))
        buckets.setdefault(key, {})[int(row["vector"])] = row

    out: dict[tuple[int, int], str] = {}
    for key, per_vector in buckets.items():
        if len(per_vector) != cfg.vectors:
            raise CampaignError(f"site {key} has {len(per_vector)} vectors, "
                                f"expected {cfg.vectors}")
        xor = np.zeros((cfg.vectors, width), dtype=np.uint8)
        delta = np.zeros(cfg.vectors, dtype=np.int32)
        timed = np.zeros(cfg.vectors, dtype=np.uint8)
        proto = np.zeros(cfg.vectors, dtype=np.uint8)
        for index in range(cfg.vectors):
            row = per_vector[index]
            observed = _hex_to_bytes(row["response"], width)
            xor[index] = observed ^ golden[index]
            delta[index] = int(row["cycles"]) - base_cycles.get(index, 0)
            timed[index] = 1 if int(row["timed_out"]) else 0
            proto[index] = 1 if int(row["protocol_error"]) else 0
        out[key] = signature_digest(cfg.circuit, xor, delta, timed, proto)
    return out


def build_catalogue(signatures: dict[tuple[int, int], str]
                    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """CSR-style catalogue: signature -> contiguous run of (site, stuck)."""
    grouped: dict[str, list[tuple[int, int]]] = {}
    for (site, stuck), digest in signatures.items():
        grouped.setdefault(digest, []).append((site, stuck))
    keys = sorted(grouped)
    if keys:
        sigs = np.frombuffer(b"".join(bytes.fromhex(k) for k in keys),
                             dtype=np.uint8).reshape(len(keys), 32).copy()
    else:
        sigs = np.empty((0, 32), np.uint8)
    sites, stucks, offsets = [], [], [0]
    for key in keys:
        for site, stuck in sorted(grouped[key]):
            sites.append(site)
            stucks.append(stuck)
        offsets.append(len(sites))
    return (sigs,
            np.array(sites, dtype=np.int32),
            np.array(stucks, dtype=np.uint8),
            np.array(offsets, dtype=np.int64))


# ------------------------------------------------------------------ campaign
def characterize(cfg: CircuitConfig, out_root: Path, *,
                 workers: int = 10, work_dir: Path | None = None,
                 yosys: str | None = None, verilator: str | None = None,
                 resume: bool = True, progress=print) -> dict[str, Any]:
    """Characterize a circuit end to end.  Returns the manifest."""
    yosys_bin = _tool("yosys", yosys)
    verilator_bin = _tool("verilator", verilator)

    out_dir = Path(out_root) / cfg.circuit
    work = Path(work_dir) if work_dir else out_dir / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    batches_dir = work / "batches"
    checkpoint_path = work / "checkpoint.json"
    started = time.time()

    checkpoint: dict[str, Any] = {}
    if resume and checkpoint_path.is_file():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("stimulus_digest") != stimulus_digest(cfg):
            raise CampaignError(
                "checkpoint was produced by a different config (stimulus "
                "digest differs).  Delete the work directory to start fresh.")

    progress(f"[1/7] synthesizing {cfg.top} with yosys")
    netlist = synthesize(cfg, work, yosys_bin)

    progress("[2/7] enumerating fault sites")
    sites = enumerate_sites(cfg.circuit, netlist, cfg.top)
    summary = site_summary(sites)
    progress(f"      {summary['sites']:,} sites, {summary['faults']:,} faults, "
             f"{summary['distinct_cell_types']} cell types")

    progress("[3/7] instrumenting netlist")
    inst_json = work / f"{cfg.circuit}_instrumented.json"
    inst_json.write_bytes(instrument_netlist(cfg.circuit, netlist, cfg.top, sites))
    inst_v = work / f"{cfg.circuit}_instrumented.v"
    if _run([yosys_bin, "-q", "-p",
             f"read_json {inst_json}; write_verilog {inst_v}"],
            work / "logs/yosys_emit.log", BUILD_TIMEOUT) != 0:
        raise CampaignError("converting the instrumented netlist to Verilog failed")

    progress("[4/7] building simulator with verilator")
    write_mem_files(cfg, work / "mem")
    testbench = work / f"tb_faultiva_{cfg.circuit}.sv"
    testbench.write_text(generate_testbench(cfg, len(sites), work / "mem"),
                         encoding="utf-8")
    binary = build_simulator(cfg, work, inst_v, testbench, verilator_bin)

    progress("[5/7] capturing fault-free baseline")
    base_csv = work / "baseline.csv"
    if _run([str(binary), "+MODE=VALIDATE", f"+CSV={base_csv}"],
            work / "logs/baseline.log", BATCH_TIMEOUT) != 0:
        raise CampaignError(
            f"baseline sweep failed; see {work/'logs/baseline.log'}.  The "
            "design did not complete a fault-free transaction - check the "
            "clock, reset polarity, start/done names and cycle_budget.")
    with open(base_csv, newline="", encoding="utf-8") as handle:
        base_rows = list(csv.DictReader(handle))
    if len(base_rows) != cfg.vectors:
        raise CampaignError(f"baseline produced {len(base_rows)} rows, "
                            f"expected {cfg.vectors}")
    width = cfg.response_bytes
    golden = np.zeros((cfg.vectors, width), dtype=np.uint8)
    golden_cycles = np.zeros(cfg.vectors, dtype=np.int32)
    for row in base_rows:
        index = int(row["vector"])
        golden[index] = _hex_to_bytes(row["response"], width)
        golden_cycles[index] = int(row["cycles"])
    timeouts = sum(1 for r in base_rows if int(r["timed_out"]))
    if timeouts == len(base_rows):
        raise CampaignError(
            "every fault-free transaction timed out - the design never "
            "asserted done.  Check the done signal name and polarity, and "
            "raise cycle_budget if the design needs longer.")
    progress(f"      {len(base_rows)} vectors, "
             f"{len({bytes(r) for r in golden})} distinct responses, "
             f"{timeouts} timeout(s)")

    # ---------------------------------------------------------------- batches
    plan = [(i, s, min(BATCH_SITES, len(sites) - s))
            for i, s in enumerate(range(0, len(sites), BATCH_SITES))]
    done: set[int] = set(checkpoint.get("completed_batches", []))
    signatures: dict[tuple[int, int], str] = {
        (int(k.split(":")[0]), int(k.split(":")[1])): v
        for k, v in checkpoint.get("signatures", {}).items()
    }
    todo = [b for b in plan if b[0] not in done]
    progress(f"[6/7] campaign: {len(plan)} batches, {len(done)} already done, "
             f"{len(todo)} to run, {workers} workers")

    def work_one(item: tuple[int, int, int]) -> tuple[int, dict]:
        batch_id, start, count = item
        rows = run_batch(binary, batches_dir, batch_id, start, count,
                         expected_rows(cfg, count))
        return batch_id, signatures_from_rows(cfg, rows, golden)

    completed = len(done)
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(work_one, item): item for item in todo}
            for future in as_completed(futures):
                batch_id, sigs = future.result()
                signatures.update(sigs)
                done.add(batch_id)
                completed += 1
                _atomic_json(checkpoint_path, {
                    "circuit": cfg.circuit,
                    "stimulus_digest": stimulus_digest(cfg),
                    "total_batches": len(plan),
                    "completed_batches": sorted(done),
                    "signatures": {f"{s}:{v}": d
                                   for (s, v), d in signatures.items()},
                })
                if completed % 10 == 0 or completed == len(plan):
                    progress(f"      {completed}/{len(plan)} batches")

    if len(signatures) != len(sites) * 2:
        raise CampaignError(f"expected {len(sites)*2} fault signatures, "
                            f"have {len(signatures)}")

    # -------------------------------------------------------------- catalogue
    progress("[7/7] writing catalogue")
    sigs, site_ids, stuck_ids, offsets = build_catalogue(signatures)
    np.savez_compressed(out_dir / "catalogue.npz", signatures=sigs,
                        sites=site_ids, stuck=stuck_ids, offsets=offsets)
    np.savez_compressed(out_dir / "golden.npz", golden=golden,
                        cycles=golden_cycles,
                        vectors=np.int32(cfg.vectors))
    np.savez_compressed(
        out_dir / "sites.npz",
        cell_name=np.array([s["cell_name"] for s in sites], dtype=object),
        cell_type=np.array([s["cell_type"] for s in sites], dtype=object),
        output_port=np.array([s["output_port"] for s in sites], dtype=object),
        net_bit=np.array([s["net_bit_id"] for s in sites], dtype=np.int64))

    sizes = np.diff(offsets)
    manifest = {
        "schema": "faultiva.user_circuit.v1",
        "circuit": cfg.circuit,
        "top_module": cfg.top,
        "protocol": cfg.protocol,
        "source": "user",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(time.time() - started, 1),
        "vectors": cfg.vectors,
        "cycle_budget": cfg.cycle_budget,
        "response_bytes": width,
        "sites": len(sites),
        "faults": len(sites) * 2,
        "distinct_signatures": int(sigs.shape[0]),
        "uniquely_localizable": round(float((sizes == 1).mean()), 6) if sizes.size else 0.0,
        "largest_candidate_set": int(sizes.max()) if sizes.size else 0,
        "cell_types": summary["cell_types"],
        "sequential_sites": summary["sequential_sites"],
        "stimulus_digest": stimulus_digest(cfg),
        "synthesis": ("custom" if cfg.synthesis_script else
                      f"builtin abc -g {cfg.abc_gates}"
                      f"{' + opt_clean' if cfg.opt_clean else ''}"),
        "synthesis_note": ("site count depends on the synthesis recipe; a "
                           "different flow yields a different but equally "
                           "valid gate-level circuit"),
        "golden_source": "fault_free_simulation_of_user_netlist",
        "golden_caveat": ("no independent software oracle; a functional bug in "
                          "the design would be inherited by the reference"),
        "v1_verification": "OUT_OF_SCOPE (V1 is trained on "
                           "opentitan_hmac_sha256 only)",
        "toolchain": {
            "yosys": yosys_bin,
            "verilator": verilator_bin,
        },
        "artifacts": {
            name: _sha(out_dir / name)
            for name in ("catalogue.npz", "golden.npz", "sites.npz")
        },
    }
    _atomic_json(out_dir / "manifest.json", manifest)

    progress(f"      {manifest['distinct_signatures']:,} signatures, "
             f"{manifest['uniquely_localizable']*100:.1f}% unique, "
             f"largest set {manifest['largest_candidate_set']:,}")
    progress(f"done in {manifest['elapsed_seconds']:.0f}s -> {out_dir}")
    return manifest
