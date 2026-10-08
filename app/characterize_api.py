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
import re
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

# The campaign prints its own phase number as "[n/7] <text>", so take the
# number from there rather than inferring it from keywords - an inferred map
# drifts from the campaign the moment its order changes, and a progress panel
# that names the wrong activity is worse than one that names none.
PHASE_NAMES = {
    1: "Synthesize netlist",
    2: "Enumerate fault sites",
    3: "Instrument fault injection",
    4: "Build simulator",
    5: "Capture fault-free baseline",
    6: "Simulate faults",
    7: "Write catalogue",
}


def _parse_progress(job: Job, line: str) -> None:
    """Update job state from one line of campaign output.

    Every pattern here is taken from the campaign's actual output, not from an
    assumption about it:

        [1/7] synthesizing siphash_core with yosys
              3,997 sites, 7,994 faults, 63 batches
        [6/7] campaign: 63 batches, 0 already done, 63 to run, 10 workers
              20/63 batches
    """
    low = line.lower()

    # phase number straight from the campaign's own label
    m = re.match(r"\s*\[(\d+)\s*/\s*7\]", low)
    if m:
        num = int(m.group(1))
        if num >= job.phase:
            job.phase = num
            job.phase_name = PHASE_NAMES.get(num, line.strip()[:48])

    # "   20/63 batches" - the count comes BEFORE the word
    m = re.search(r"(\d+)\s*/\s*(\d+)\s+batches", low)
    if m:
        job.batches_done = int(m.group(1))
        job.batches_total = int(m.group(2))
    else:
        # "[6/7] campaign: 63 batches, ..." establishes the total up front, so
        # the bar has a denominator before the first completion report
        m = re.search(r"campaign:\s*([\d,]+)\s+batches", low)
        if m and not job.batches_total:
            job.batches_total = int(m.group(1).replace(",", ""))

    m = re.search(r"([\d,]+)\s+sites", low)
    if m and not job.sites:
        job.sites = int(m.group(1).replace(",", ""))
    m = re.search(r"([\d,]+)\s+faults", low)
    if m and not job.faults_total:
        job.faults_total = int(m.group(1).replace(",", ""))

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

    Never inside the installation: a frozen app may live in a read-only
    directory, and a catalogue the user built is their data.  Takes the first
    entry of the engine's own resolution order so the writer and the reader can
    never disagree about the location - they did once, and a finished campaign
    was invisible.
    """
    from faultiva import Faultiva
    return Faultiva.user_circuit_roots()[0]


# ───────────────────────── preflight ─────────────────────────

async def _read_sources(form) -> tuple[list[tuple[str, str]], str | None]:
    """Every uploaded Verilog file, as (filename, text).

    A design is often a wrapper plus the modules it instantiates, so more than
    one part may arrive under the same field name.  Returns an error message
    instead of the list when something is unusable.
    """
    uploads = [u for u in form.getlist("rtl") if hasattr(u, "read")]
    if not uploads:
        return [], "no Verilog file supplied"

    total = 0
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for upload in uploads:
        raw = await upload.read()
        total += len(raw)
        if total > 8 * 1024 * 1024:
            return [], "files larger than 8 MB in total"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            name = Path(getattr(upload, "filename", "design.v")).name
            return [], f"{name} is not UTF-8 text"
        name = Path(getattr(upload, "filename", "design.v")).name
        if name in seen:          # same file picked twice
            continue
        seen.add(name)
        out.append((name, text))
    return out, None


def _infer_top(sources: list[tuple[str, str]]) -> str:
    """The module nothing else instantiates.

    With several files the top is the one no other module uses, which is a
    better guess than "last module in the file" once a wrapper is involved.
    """
    import re
    declared: list[str] = []
    for _, text in sources:
        declared += re.findall(r"^\s*module\s+([A-Za-z_]\w*)", text, re.M)
    if not declared:
        return Path(sources[0][0]).stem if sources else "top"

    instantiated: set[str] = set()
    for _, text in sources:
        for mod in declared:
            # `mod instance_name (` - a declaration is `module mod`
            if re.search(r"^\s*(?!module\b)" + re.escape(mod) + r"\s+[A-Za-z_]\w*\s*\(",
                         text, re.M):
                instantiated.add(mod)
    roots = [m for m in declared if m not in instantiated]
    return roots[-1] if roots else declared[-1]


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
    sources, problem = await _read_sources(form)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

    top = str(form.get("top") or "").strip()
    scratch = Path(tempfile.mkdtemp(prefix="faultiva_pre_"))
    try:
        # side by side, so a relative `include` resolves the way it would in
        # the user's own tree
        rtl_paths = []
        for name, text in sources:
            p = scratch / name
            p.write_text(text, encoding="utf-8")
            rtl_paths.append(p)

        # Reuse the campaign's own synthesis and enumeration, so the preflight
        # count is the number the real run will use, not an approximation.
        from faultiva.characterize import campaign as cp
        from faultiva.characterize import netlist as nl
        from faultiva.characterize.config import parse_config

        if not top:
            top = _infer_top(sources)

        # Preflight only needs synthesis, so the stimulus description can be
        # nominal; it is never simulated here.  The names still have to parse,
        # so take them from the form when supplied.
        cfg = parse_config({
            "circuit": "preflight",
            "top": top,
            "rtl": [str(p) for p in rtl_paths],
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

def _normalise_constants(raw) -> list[dict]:
    """Convert a convenient constants shape into the schema's own.

    The parser requires a list of {name, width, value}; a form naturally
    produces {"mode": 1}.  Accept either, and accept "name": [width, value]
    when the pin is wider than its value needs.
    """
    if isinstance(raw, list):
        return raw                      # already in schema form
    if not isinstance(raw, dict):
        raise ValueError("expected a mapping or a list")

    out = []
    for name, value in raw.items():
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError(f"{name}: expected [width, value]")
            width, held = int(value[0]), int(value[1])
        elif isinstance(value, dict):
            out.append(value)
            continue
        else:
            held = 1 if value is True else 0 if value is False else int(value)
            width = max(1, held.bit_length())
        if held < 0:
            raise ValueError(f"{name}: must be >= 0")
        if held >= (1 << width):
            raise ValueError(f"{name}: {held} does not fit in {width} bit(s)")
        out.append({"name": name, "width": width, "value": held})
    return out


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
    sources, problem = await _read_sources(form)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

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

    rtl_paths = []
    for name, text in sources:
        p = work / name
        p.write_text(text, encoding="utf-8")
        rtl_paths.append(p)

    try:
        in_width = max(1, int(str(form.get("data_in_width") or "32")))
        out_width = max(1, int(str(form.get("data_out_width") or "32")))
    except ValueError:
        return JSONResponse({"error": "port widths must be whole numbers"},
                            status_code=400)

    config = {
        "circuit": circuit,
        "rtl": [str(p) for p in rtl_paths],
        "top": str(form.get("top") or "").strip() or _infer_top(sources),
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
            parsed = json.loads(constants)
        except json.JSONDecodeError:
            return JSONResponse({"error": "constants must be JSON"},
                                status_code=400)
        try:
            config["constants"] = _normalise_constants(parsed)
        except ValueError as exc:
            return JSONResponse({"error": f"constants: {exc}"},
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
        "# optional: configuration pins held at a fixed value for the whole\n"
        "# campaign - width is required, because the pin has to be driven\n"
        "# constants:\n"
        "#   - {name: mode, width: 1, value: 1}\n"
    )
    return JSONResponse({"filename": "my_core.yaml", "text": template})
