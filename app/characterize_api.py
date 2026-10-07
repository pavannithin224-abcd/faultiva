"""Characterization HTTP endpoints for the dashboard and desktop app.

Three things the UI needs and did not have:

    GET  /api/toolchain   can this machine characterize a circuit, and if not
                          what should the user install
    POST /api/preflight   synthesize an uploaded design and report its size and
                          an honest time estimate, without starting a campaign
    POST /api/characterize  start a campaign
    GET  /api/characterize/status   progress for the running campaign
    POST /api/characterize/cancel   stop it

Running Yosys and Verilator on uploaded Verilog is arbitrary code execution by
design: Verilator emits C++ and invokes a compiler.  Every endpoint here is
therefore refused unless the server is bound to loopback.  The desktop app
always binds 127.0.0.1; the web dashboard has to be started that way on
purpose.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

# how long a single preflight synthesis may take before we give up on it
_PREFLIGHT_TIMEOUT = 180

# measured on the reference machine: seconds per fault per worker, from the
# 92-batch sha256_generic run (5,848 sites / 11,696 faults / ~47 min / 10
# workers).  Used only for an estimate the UI labels as approximate.
_SECONDS_PER_FAULT_PER_WORKER = 2.4


def _loopback_only(request: Request) -> bool:
    """True when this server is reachable only from this machine."""
    if os.environ.get("FAULTIVA_ALLOW_REMOTE_CHARACTERIZE") == "1":
        return True
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


def _refused() -> JSONResponse:
    return JSONResponse(
        {"error": "characterization is available only on a local session",
         "detail": ("Characterization compiles and runs code generated from "
                    "the uploaded design, so it is refused over the network. "
                    "Start the server on 127.0.0.1, or use the desktop app, "
                    "which always does."),
         "code": "LOCAL_ONLY"},
        status_code=403)


# ───────────────────────── toolchain ─────────────────────────

async def api_toolchain(request: Request) -> JSONResponse:
    """Report whether Yosys and Verilator are usable here."""
    from faultiva import toolchain as tc
    chain = tc.detect()
    payload = chain.as_dict()
    payload["local_session"] = _loopback_only(request)
    payload["labels"] = {
        "yosys": chain.yosys.label,
        "verilator": chain.verilator.label,
    }
    return JSONResponse(payload)


# ───────────────────────── job state ─────────────────────────

@dataclass
class Job:
    """One characterization run, and everything the UI shows about it."""
    job_id: str
    circuit: str
    workdir: Path
    phase: int = 0
    phase_name: str = "queued"
    batches_done: int = 0
    batches_total: int = 0
    faults_total: int = 0
    sites: int = 0
    failures: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    status: str = "running"          # running | done | failed | cancelled
    error: str | None = None
    lines: list[str] = field(default_factory=list)
    process: subprocess.Popen | None = None

    def snapshot(self) -> dict:
        elapsed = (self.finished or time.time()) - self.started
        remaining = None
        if self.status == "running" and self.batches_done and self.batches_total:
            per = elapsed / self.batches_done
            remaining = max(0.0, per * (self.batches_total - self.batches_done))
        return {
            "job_id": self.job_id,
            "circuit": self.circuit,
            "status": self.status,
            "phase": self.phase,
            "phase_name": self.phase_name,
            "batches_done": self.batches_done,
            "batches_total": self.batches_total,
            "sites": self.sites,
            "faults_total": self.faults_total,
            "failures": self.failures,
            "elapsed_s": round(elapsed, 1),
            "remaining_s": round(remaining, 1) if remaining is not None else None,
            "error": self.error,
            "log": self.lines[-14:],
        }


_JOB: Job | None = None
_JOB_LOCK = threading.Lock()

_PHASES = {
    "synthesiz": (1, "Synthesize netlist"),
    "enumerat": (2, "Enumerate fault sites"),
    "instrument": (3, "Instrument fault injection"),
    "build": (4, "Build simulator"),
    "simulat": (5, "Simulate faults"),
    "catalogue": (6, "Build signature catalogue"),
    "round trip": (7, "Verify round trip"),
    "verify": (7, "Verify round trip"),
}


def _parse_progress(job: Job, line: str) -> None:
    """Update job state from one line of campaign output."""
    low = line.lower()
    for needle, (num, name) in _PHASES.items():
        if needle in low:
            if num >= job.phase:
                job.phase, job.phase_name = num, name
            break

    # "[5/7] batch 58/92" and "5,848 sites" style lines
    import re
    m = re.search(r"batch\s+(\d+)\s*/\s*(\d+)", low)
    if m:
        job.batches_done = int(m.group(1))
        job.batches_total = int(m.group(2))
    m = re.search(r"([\d,]+)\s+sites", low)
    if m and not job.sites:
        job.sites = int(m.group(1).replace(",", ""))
    m = re.search(r"([\d,]+)\s+faults", low)
    if m and not job.faults_total:
        job.faults_total = int(m.group(1).replace(",", ""))
    if "fail" in low and "0 fail" not in low:
        m = re.search(r"(\d+)\s+failures?", low)
        if m:
            job.failures = int(m.group(1))


def _runner(job: Job, config: Path, out: Path, workers: int) -> None:
    """Drive the characterize CLI and stream its progress into the job."""
    cmd = [sys.executable, "-u", "-m", "faultiva.characterize",
           "--config", str(config), "--out", str(out),
           "--workers", str(workers)]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    try:
        job.process = subprocess.Popen(
            cmd, cwd=str(_repo_root()), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        assert job.process.stdout is not None
        for raw in job.process.stdout:
            line = raw.rstrip()
            if not line:
                continue
            job.lines.append(line)
            _parse_progress(job, line)
        code = job.process.wait()
        if job.status == "cancelled":
            pass
        elif code == 0:
            job.status = "done"
            job.phase, job.phase_name = 7, "complete"
        else:
            job.status = "failed"
            job.error = f"characterization exited {code}"
    except Exception as exc:                      # noqa: BLE001
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished = time.time()


def _repo_root() -> Path:
    stated = os.environ.get("FAULTIVA_ROOT")
    if stated and (Path(stated) / "faultiva").is_dir():
        return Path(stated)
    return Path(__file__).resolve().parents[1]


def _user_dir() -> Path:
    """Where characterized circuits are written.

    Never inside the installation: a frozen app lives in Program Files or a
    read-only bundle directory, and the catalogue belongs to the user.
    """
    stated = os.environ.get("FAULTIVA_USER_CIRCUITS")
    if stated:
        return Path(stated)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Faultiva" / "user_circuits"


# ───────────────────────── preflight ─────────────────────────

async def api_preflight(request: Request) -> JSONResponse:
    """Synthesize an uploaded design and report its size, without simulating."""
    if not _loopback_only(request):
        return _refused()

    from faultiva import toolchain as tc
    chain = tc.detect()
    if not chain.ready:
        return JSONResponse({"error": "toolchain not available",
                             "toolchain": chain.as_dict()}, status_code=409)

    form = await request.form()
    upload = form.get("rtl")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "no Verilog file supplied"},
                            status_code=400)

    raw = await upload.read()
    if len(raw) > 8 * 1024 * 1024:
        return JSONResponse({"error": "file larger than 8 MB"}, status_code=400)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"error": "file is not UTF-8 text"},
                            status_code=400)

    name = Path(getattr(upload, "filename", "design.v")).name
    top = str(form.get("top") or "").strip()
    scratch = Path(tempfile.mkdtemp(prefix="faultiva_pre_"))
    try:
        rtl = scratch / name
        rtl.write_text(text, encoding="utf-8")

        # Reuse the campaign's own synthesis and enumeration, so the preflight
        # count is the number the real run will use, not an approximation.
        from faultiva.characterize import campaign as cp
        from faultiva.characterize import netlist as nl
        from faultiva.characterize.config import parse_config

        if not top:
            import re
            found = re.findall(r"^\s*module\s+([A-Za-z_]\w*)", text, re.M)
            top = found[-1] if found else Path(name).stem

        # Preflight only needs synthesis, so the stimulus description can be
        # nominal; it is never simulated here.  The names still have to parse,
        # so take them from the form when supplied.
        cfg = parse_config({
            "circuit": "preflight",
            "top": top,
            "rtl": [str(rtl)],
            "clock": str(form.get("clock") or "clk"),
            "reset": {"name": str(form.get("reset") or "rst_n"),
                      "active_low": str(form.get("reset_active_low")
                                        or "1") == "1"},
            "protocol": "simple_handshake",
            "start": {"name": str(form.get("start") or "start"),
                      "pulse": True},
            "done": str(form.get("done") or "done"),
            "inputs": [{"name": str(form.get("data_in") or "data_i"),
                        "width": 32}],
            "output": {"name": str(form.get("data_out") or "result_o"),
                       "width": 32},
            "vectors": 8,
        }, scratch)

        work = scratch / "work"
        work.mkdir(parents=True, exist_ok=True)
        began = time.time()
        netlist_path = cp.synthesize(cfg, work, chain.yosys.path)
        seconds = time.time() - began

        sites = nl.enumerate_sites(cfg.circuit, netlist_path, cfg.top)
        summary = nl.site_summary(sites)

        count = len(sites)
        faults = count * 2
        batches = (count + 63) // 64
        workers = max(1, min(10, (os.cpu_count() or 4)))
        estimate = faults * _SECONDS_PER_FAULT_PER_WORKER / workers

        return JSONResponse({
            "ok": True,
            "top": top,
            "file": name,
            "lines": text.count("\n") + 1,
            "sites": count,
            "faults": faults,
            "batches": batches,
            "cell_types": summary.get("cell_types"),
            "distinct_cell_types": summary.get("distinct_cell_types"),
            "sequential_sites": summary.get("sequential_sites"),
            "combinational_sites": summary.get("combinational_sites"),
            "synthesis_s": round(seconds, 1),
            "workers": workers,
            "estimate_s": int(estimate),
            "estimate_text": _human(estimate),
        })
    except Exception as exc:                      # noqa: BLE001
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"},
                            status_code=400)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds"
    if seconds < 5400:
        return f"{int(round(seconds / 60))} minutes"
    return f"{seconds / 3600:.1f} hours"


# ───────────────────────── campaign ─────────────────────────

async def api_characterize(request: Request) -> JSONResponse:
    """Start a characterization campaign."""
    global _JOB
    if not _loopback_only(request):
        return _refused()

    with _JOB_LOCK:
        if _JOB is not None and _JOB.status == "running":
            return JSONResponse(
                {"error": "a characterization is already running",
                 "job": _JOB.snapshot()}, status_code=409)

    from faultiva import toolchain as tc
    chain = tc.detect()
    if not chain.ready:
        return JSONResponse({"error": "toolchain not available",
                             "toolchain": chain.as_dict()}, status_code=409)

    form = await request.form()
    upload = form.get("rtl")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "no Verilog file supplied"},
                            status_code=400)
    raw = await upload.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"error": "file is not UTF-8 text"},
                            status_code=400)

    circuit = str(form.get("circuit") or "").strip()
    if not circuit or not circuit.replace("_", "").isalnum():
        return JSONResponse(
            {"error": "circuit name must be letters, digits and underscores"},
            status_code=400)

    from faultiva import Faultiva
    if circuit in Faultiva(_repo_root()).families:
        return JSONResponse(
            {"error": f"'{circuit}' is already a circuit in this install"},
            status_code=400)

    out = _user_dir()
    out.mkdir(parents=True, exist_ok=True)
    work = out / circuit / "_src"
    work.mkdir(parents=True, exist_ok=True)

    name = Path(getattr(upload, "filename", "design.v")).name
    rtl = work / name
    rtl.write_text(text, encoding="utf-8")

    try:
        in_width = max(1, int(str(form.get("data_in_width") or "32")))
        out_width = max(1, int(str(form.get("data_out_width") or "32")))
    except ValueError:
        return JSONResponse({"error": "port widths must be whole numbers"},
                            status_code=400)

    config = {
        "circuit": circuit,
        "rtl": [str(rtl)],
        "top": str(form.get("top") or "").strip() or Path(name).stem,
        "clock": str(form.get("clock") or "clk"),
        "reset": {"name": str(form.get("reset") or "rst_n"),
                  "active_low": str(form.get("reset_active_low")
                                    or "1") == "1"},
        "protocol": "simple_handshake",
        "start": {"name": str(form.get("start") or "start"), "pulse": True},
        "done": str(form.get("done") or "done"),
        "inputs": [{"name": str(form.get("data_in") or "data_i"),
                    "width": in_width}],
        "output": {"name": str(form.get("data_out") or "result_o"),
                   "width": out_width},
        "vectors": int(str(form.get("vectors") or "64") or 64),
    }
    budget = str(form.get("cycle_budget") or "").strip()
    if budget:
        try:
            config["cycle_budget"] = int(budget)
        except ValueError:
            return JSONResponse({"error": "cycle budget must be a number"},
                                status_code=400)
    constants = str(form.get("constants") or "").strip()
    if constants:
        try:
            config["constants"] = json.loads(constants)
        except json.JSONDecodeError:
            return JSONResponse({"error": "constants must be JSON"},
                                status_code=400)

    import yaml
    config_path = work / f"{circuit}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False),
                           encoding="utf-8")

    workers = max(1, min(16, int(str(form.get("workers") or "10") or 10)))
    job = Job(job_id=uuid.uuid4().hex[:12], circuit=circuit, workdir=out)
    with _JOB_LOCK:
        _JOB = job
    threading.Thread(target=_runner, args=(job, config_path, out, workers),
                     daemon=True).start()
    return JSONResponse({"started": True, "job": job.snapshot()})


async def api_characterize_status(request: Request) -> JSONResponse:
    if _JOB is None:
        return JSONResponse({"job": None})
    return JSONResponse({"job": _JOB.snapshot()})


async def api_characterize_cancel(request: Request) -> JSONResponse:
    if not _loopback_only(request):
        return _refused()
    job = _JOB
    if job is None or job.status != "running":
        return JSONResponse({"error": "nothing is running"}, status_code=409)
    job.status = "cancelled"
    if job.process is not None:
        job.process.terminate()
    return JSONResponse({"cancelled": True, "job": job.snapshot()})


async def api_config_template(request: Request) -> JSONResponse:
    """The YAML a user needs in order to run characterization themselves."""
    template = (
        "# Faultiva circuit description\n"
        "#   python -m faultiva.characterize --config my_core.yaml --check\n"
        "#   python -m faultiva.characterize --config my_core.yaml\n"
        "\n"
        "circuit: my_core             # name it appears under in Faultiva\n"
        "top: my_core                 # top module inside the RTL\n"
        "rtl:\n"
        "  - rtl/my_core.v            # paths are relative to this file\n"
        "\n"
        "clock: clk\n"
        "reset: {name: rst_n, active_low: true}\n"
        "\n"
        "protocol: simple_handshake\n"
        "start: {name: start, pulse: true}   # high for one cycle\n"
        "done: done                          # polled until asserted\n"
        "\n"
        "inputs:                      # fresh deterministic stimulus per vector\n"
        "  - {name: data_i, width: 32}\n"
        "\n"
        "output: {name: result_o, width: 32}  # compared against fault-free\n"
        "\n"
        "vectors: 64                  # test patterns, derived deterministically\n"
        "cycle_budget: 200            # generous upper bound per operation\n"
        "\n"
        "# optional: configuration pins held at a fixed value\n"
        "# constants:\n"
        "#   mode: 1\n"
    )
    return JSONResponse({"filename": "my_core.yaml", "text": template})
