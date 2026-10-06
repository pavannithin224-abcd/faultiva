"""Faultiva — local analysis server.

Starts a small HTTP server on 127.0.0.1 and opens the interface in your
browser. Nothing leaves this machine; there is no network access and no
telemetry.

    python app.py              start and open the browser
    python app.py --no-browser start only
    python app.py --port 8000  choose the port
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
REPO = HERE.parent if (HERE.parent / "faultiva").is_dir() else HERE
sys.path.insert(0, str(REPO))

from faultiva import Faultiva  # noqa: E402

REPO_URL = "https://github.com/pavannithin224-abcd/faultiva"
MAX_BYTES = 64 * 1024 * 1024

_f: Faultiva | None = None
_lock = threading.Lock()


_meta: dict = {}


def meta() -> dict:
    """Polarity and cell-type tables, loaded once."""
    if not _meta:
        f = engine()
        z = np.load(Path(f.root) / "models/faultiva_signature_dictionary.npz",
                    allow_pickle=True)
        _meta["stuck"] = {c: z[f"{c}__stuck"] for c in f.families
                          if f"{c}__stuck" in z}
        try:
            ct = np.load(Path(f.root) / "data/faultiva_cell_types.npz",
                         allow_pickle=True)
            _meta["cell_code"] = {
                c.replace("__cell_code", ""): ct[c] for c in ct.files
                if c.endswith("__cell_code")}
            _meta["vocab"] = [str(x) for x in ct["cell_vocabulary"]]
        except Exception:
            _meta["cell_code"], _meta["vocab"] = {}, []
        try:
            nm = np.load(Path(f.root) / "data/faultiva_site_names.npz",
                         allow_pickle=True)
            _meta["net"] = {k.replace("__net_name", ""): nm[k]
                            for k in nm.files if k.endswith("__net_name")}
            _meta["cat"] = {k.replace("__category", ""): nm[k]
                            for k in nm.files if k.endswith("__category")}
        except Exception:
            _meta["net"], _meta["cat"] = {}, {}
    return _meta


def net_of(family: str, site: int):
    """Real net name and site category, where the campaign recorded them."""
    m = meta()
    nt = m.get("net", {}).get(family)
    ct = m.get("cat", {}).get(family)
    name = str(nt[site]) if nt is not None and 0 <= site < nt.size else ""
    cat = str(ct[site]) if ct is not None and 0 <= site < ct.size else ""
    return (name or None), (cat or None)


def engine() -> Faultiva:
    global _f
    with _lock:
        if _f is None:
            _f = Faultiva(REPO)
            # warm the verification model so the first analysis is not slow
            rng = np.random.default_rng(0)
            _f.verify("opentitan_hmac_sha256", [0], 1,
                      rng.integers(0, 2, 256), rng.integers(0, 2, 256))
    return _f


# --------------------------------------------------------------- parsing
def parse_capture(raw: bytes, n_vectors: int, label: str) -> dict[str, np.ndarray]:
    """Read a capture file.

    Accepted layouts, one record per line:

        <64 hex chars>                                  response only
        <64 hex chars> <timeout> <protocol> <cycles>    full four-channel record

    Comment lines starting with '#' and blank lines are ignored.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise ValueError(f"{label}: not readable as text ({exc})")

    rows = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    if not rows:
        raise ValueError(f"{label}: no usable lines")
    if len(rows) != n_vectors:
        raise ValueError(
            f"{label}: {len(rows)} records but this circuit's vector plan has "
            f"{n_vectors}. A capture must cover the whole plan.")

    response = np.zeros((n_vectors, 32), dtype=np.uint8)
    timeout = np.zeros(n_vectors, dtype=np.uint8)
    protocol = np.zeros(n_vectors, dtype=np.uint8)
    cycles = np.zeros(n_vectors, dtype=np.int32)
    channels_supplied = False

    for i, row in enumerate(rows):
        parts = row.replace(",", " ").split()
        hexpart = parts[0]
        if len(hexpart) != 64:
            raise ValueError(
                f"{label} line {i + 1}: expected 64 hex characters "
                f"(32 response bytes), got {len(hexpart)}")
        try:
            response[i] = np.frombuffer(bytes.fromhex(hexpart), dtype=np.uint8)
        except ValueError:
            raise ValueError(f"{label} line {i + 1}: not valid hexadecimal")
        if len(parts) >= 4:
            channels_supplied = True
            try:
                timeout[i] = int(parts[1])
                protocol[i] = int(parts[2])
                cycles[i] = int(parts[3])
            except ValueError:
                raise ValueError(
                    f"{label} line {i + 1}: timeout, protocol and cycles must "
                    f"be integers")

    return {"response": response, "timeout": timeout, "protocol": protocol,
            "cycles": cycles, "channels_supplied": channels_supplied}


def netlist_cells(netlist: dict) -> list[dict]:
    """Flatten a yosys netlist into an ordered cell table.

    Site indices in the catalogue are ranks over the cell list, so this gives
    real names and types for candidate sites instead of bare integers.
    """
    out = []
    for mod_name, mod in netlist.get("modules", {}).items():
        for cell_name, cell in (mod.get("cells") or {}).items():
            out.append({"name": cell_name,
                        "type": cell.get("type", "?"),
                        "module": mod_name})
    return out


def check_netlist(obj) -> dict:
    """Validate that parsed JSON is actually a yosys netlist."""
    if not isinstance(obj, dict):
        raise ValueError(
            "this JSON is a %s, not a yosys netlist. A netlist has a top-level "
            '"modules" object -- produce one with yosys write_json.'
            % type(obj).__name__)
    if "modules" not in obj:
        keys = ", ".join(list(obj.keys())[:5]) or "none"
        raise ValueError(
            'no "modules" key, so this is not a yosys netlist '
            "(top-level keys: %s)." % keys)
    if not isinstance(obj["modules"], dict):
        raise ValueError('"modules" must be an object mapping module names '
                         "to definitions.")
    return obj


def identify(netlist: dict, known: list[str]) -> tuple[str | None, str]:
    """Match an uploaded netlist to a characterized circuit.

    Deliberately strict: a wrapper whose name merely contains 'aes' is not the
    characterized AES design, and localizing it against the wrong catalogue
    would produce confident nonsense.
    """
    modules = list(netlist.get("modules", {}).keys())
    if not modules:
        return None, "netlist contains no modules"
    blob = " ".join(modules).lower()

    exact = [("opentitan_hmac", "opentitan_hmac_sha256"),
             ("hmac", "opentitan_hmac_sha256"),
             ("picorv32", "picorv32_cpu")]
    for needle, family in exact:
        if needle in blob:
            return family, f"module name contains '{needle}'"

    for needle, family in (("aes", "secworks_aes"), ("sha256", "secworks_sha256")):
        if needle in blob:
            if any(m.lower().startswith(needle) for m in modules):
                return family, f"module name matches the characterized {family}"
            return None, (f"module '{modules[0]}' mentions '{needle}' but is not "
                          f"the characterized {family} design")
    return None, f"no characterized circuit matches '{modules[0]}'"


# --------------------------------------------------------------- routes
async def api_circuits(request: Request) -> JSONResponse:
    f = engine()
    out = []
    for c in f.families:
        sizes = np.diff(f._offsets[c])
        out.append({
            "id": c,
            "signatures": int(f.signature_count(c)),
            "vectors": f.vectors(c) if c in f._vectors else None,
            "uniquely_localizable": round(float((sizes == 1).mean()), 4),
            "largest_set": int(sizes.max()),
        })
    return JSONResponse({"circuits": out, "repository": REPO_URL})


async def api_example(request: Request) -> JSONResponse:
    """Return a real campaign capture, formatted as an uploadable file."""
    f = engine()
    family = request.query_params.get("circuit", "secworks_sha256")
    caps = np.load(Path(f.root) / "data/faultiva_example_captures.npz",
                   allow_pickle=True)
    if f"{family}__response" not in caps:
        return JSONResponse({"error": f"no example capture for {family}"}, 404)
    resp = caps[f"{family}__response"]
    to = caps[f"{family}__timeout"]
    pe = caps[f"{family}__protocol"]
    cy = caps[f"{family}__cycles"]
    lines = ["# Faultiva capture — response(hex32) timeout protocol cycles",
             f"# circuit: {family}"]
    for i in range(resp.shape[0]):
        lines.append(f"{resp[i].tobytes().hex()} {int(to[i])} {int(pe[i])} {int(cy[i])}")
    return JSONResponse({"circuit": family, "filename": f"{family}_capture.txt",
                         "content": "\n".join(lines)})


async def api_analyse(request: Request) -> JSONResponse:
    t0 = time.perf_counter()
    f = engine()
    form = await request.form()

    circuit = form.get("circuit") or ""
    netlist_file = form.get("netlist")
    capture_file = form.get("capture")
    cell_table: list[dict] = []

    result: dict[str, Any] = {"repository": REPO_URL}

    # ---- circuit identity -------------------------------------------------
    if netlist_file is not None and hasattr(netlist_file, "read"):
        raw = await netlist_file.read()
        if len(raw) > MAX_BYTES:
            return JSONResponse({"error": "netlist exceeds 64 MB"}, 413)
        try:
            netlist = check_netlist(
                json.loads(raw.decode("utf-8", errors="replace")))
        except json.JSONDecodeError as exc:
            return JSONResponse(
                {"error": f"netlist is not valid JSON ({exc.msg}, line "
                          f"{exc.lineno}). Expected yosys 'write_json' output."}, 400)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        family, reason = identify(netlist, f.families)
        cell_table = netlist_cells(netlist)
        modules = list(netlist.get("modules", {}).keys())[:4]
        result["circuit"] = {"identified": family, "reason": reason,
                             "modules": modules, "cells": len(cell_table),
                             "source": "netlist"}
    elif circuit in f.families:
        family = circuit
        result["circuit"] = {"identified": family, "reason": "selected manually",
                             "modules": [], "cells": None, "source": "selected"}
    else:
        return JSONResponse(
            {"error": "provide a netlist file or select a circuit"}, 400)

    if family is None:
        result["detection"] = {"verdict": "NOT_ATTEMPTED"}
        result["localization"] = {
            "status": "UNAVAILABLE",
            "reason": "localization needs a fault catalogue for this circuit; "
                      "detection works on any design, localization does not"}
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse(result)

    # ---- capture ----------------------------------------------------------
    if capture_file is None or not hasattr(capture_file, "read"):
        return JSONResponse({"error": "no capture file supplied"}, 400)
    raw = await capture_file.read()
    if len(raw) > MAX_BYTES:
        return JSONResponse({"error": "capture exceeds 64 MB"}, 413)
    try:
        cap = parse_capture(raw, f.vectors(family), "capture")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, 400)

    golden = f.golden(family)
    differing = np.flatnonzero((cap["response"] != golden).any(axis=1))
    faulty = differing.size > 0

    result["detection"] = {
        "verdict": "FAULT_DETECTED" if faulty else "FAULT_FREE",
        "differing_vectors": int(differing.size),
        "total_vectors": int(f.vectors(family)),
        "first_differing": int(differing[0]) if faulty else None,
        "differing_list": differing[:64].tolist(),
    }
    result["capture"] = {"channels_supplied": cap["channels_supplied"]}

    if not faulty:
        result["localization"] = {"status": "NOT_APPLICABLE",
                                  "reason": "no fault detected"}
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse(result)

    # ---- localization -----------------------------------------------------
    key = f.signature(family, cap["response"], golden,
                      cycle_delta=cap["cycles"], timed_out=cap["timeout"],
                      protocol_error=cap["protocol"])
    sites = f.localize(family, key)

    if sites.size == 0:
        result["localization"] = {
            "status": "UNKNOWN_SIGNATURE",
            "signature": key,
            "reason": "this behaviour is not in the catalogue for this circuit; "
                      "Faultiva does not guess a location",
            "hint": (None if cap["channels_supplied"] else
                     "this capture supplied response bytes only; timeout and "
                     "protocol flags are part of the signature and vary per "
                     "fault, so a response-only capture rarely matches"),
        }
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return JSONResponse(result)

    m = meta()
    # recover the polarity slice for this signature
    z = np.load(Path(f.root) / "models/faultiva_signature_dictionary.npz",
                allow_pickle=True)
    sigs = z[f"{family}__signatures"]
    offs = z[f"{family}__offsets"]
    i = int(np.searchsorted(sigs, key))
    a, b = int(offs[i]), int(offs[i + 1])
    stuck = m["stuck"].get(family)
    pol = stuck[a:b] if stuck is not None else np.zeros(b - a, dtype=np.uint8)
    kinds = sorted({int(x) for x in pol.tolist()})
    type_names = ["SA0" if k == 0 else "SA1" for k in kinds]

    faults = []
    for s, t in zip(sites[:200].tolist(), pol[:200].tolist()):
        net, cat = net_of(family, int(s))
        entry = {"site": int(s),
                 "type": "SA1" if int(t) else "SA0",
                 "cell_type": cell_of(family, int(s)),
                 "net_name": net,
                 "category": cat}
        faults.append(entry)

    result["localization"] = {
        "status": "EXACT" if sites.size == 1 else "AMBIGUOUS",
        "signature": key,
        "fault_count": int(sites.size),
        "fault_types": type_names,
        "type_certain": len(kinds) == 1,
        "faults": faults,
        "truncated": bool(sites.size > 200),
        "note": (None if sites.size == 1 else
                 f"{sites.size} faults produce identical observable behaviour "
                 f"and cannot be separated from responses alone"),
    }

    # ---- verification -----------------------------------------------------
    if family == "opentitan_hmac_sha256":
        rng = np.random.default_rng(0)
        verdict, detail = f.verify(family, sites[:10], 1,
                                   rng.integers(0, 2, 256),
                                   rng.integers(0, 2, 256))
        result["verification"] = {
            "verdict": verdict, "scope": "in_scope",
            "sites_scored": int(detail.get("candidates", 0)),
            "above_threshold": int(detail.get("above_threshold", 0)),
            "threshold": detail.get("threshold"),
            "note": "structural model, independent of the behaviour evidence; "
                    "disagreement occurs on about 29% of cases by design",
        }
    else:
        result["verification"] = {
            "verdict": "OUT_OF_SCOPE", "scope": "out_of_scope",
            "note": "the verification model was trained on opentitan_hmac_sha256 "
                    "only and does not extrapolate",
        }

    result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return JSONResponse(result)


def cell_of(family: str, site: int) -> str | None:
    """Cell type driving a candidate site, when a table is available."""
    m = meta()
    table = m.get("cell_code", {}).get(family)
    vocab = m.get("vocab", [])
    if table is None or not vocab or not (0 <= site < table.size):
        return None
    code = int(table[site])
    return vocab[code] if 0 <= code < len(vocab) else None


async def index(request: Request) -> FileResponse:
    return FileResponse(HERE / "ui" / "index.html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


routes = [
    Route("/", index),
    Route("/api/circuits", api_circuits),
    Route("/api/example", api_example),
    Route("/api/analyse", api_analyse, methods=["POST"]),
    Mount("/static", StaticFiles(directory=HERE / "ui"), name="static"),
]

async def on_error(request, exc):
    """Any unhandled error still returns JSON, never an HTML error page."""
    import traceback
    traceback.print_exc()
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error": f"internal error: {type(exc).__name__}: {exc}"},
            status_code=500)
    return PlainTextResponse("internal error", status_code=500)


app = Starlette(debug=False, routes=routes,
                exception_handlers={Exception: on_error})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=7865)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()

    import uvicorn
    url = f"http://{a.host}:{a.port}"
    print(f"\n  Faultiva — local analysis server")
    print(f"  {url}")
    print(f"  nothing leaves this machine\n")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
