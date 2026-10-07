"""Install, run, verify, uninstall - the test that matters.

A setup file that compiles proves nothing.  This installs it the way a user
would (silently, per-user), launches the installed executable rather than the
build output, diagnoses all four circuits through the HTTP API, compares the
answers against the frozen-build run, then uninstalls and checks the directory
is gone.

Anything that is not an exact match is a failure, printed as such.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BUILD = Path(os.environ.get("FAULTIVA_BUILD", r"C:\Users\1\faultiva_build"))
SETUP = BUILD / "installer" / "Faultiva-Setup-1.0.0.exe"
TARGET = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Faultiva"
PORT = 7941


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def diagnose(base: str) -> dict:
    out = {}
    for fam in ("opentitan_hmac_sha256", "picorv32_cpu",
                "secworks_aes", "secworks_sha256"):
        ex = json.loads(urllib.request.urlopen(
            f"{base}/api/example?circuit={fam}", timeout=120).read())
        text = ex.get("text") or ex.get("content") or ""
        b = "----faultiva"
        body = (
            f"--{b}\r\nContent-Disposition: form-data; name=\"circuit\"\r\n\r\n"
            f"{fam}\r\n--{b}\r\nContent-Disposition: form-data; "
            f"name=\"capture\"; filename=\"c.txt\"\r\n"
            f"Content-Type: text/plain\r\n\r\n{text}\r\n--{b}--\r\n"
        ).encode()
        req = urllib.request.Request(
            base + "/api/analyse", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        d = json.loads(urllib.request.urlopen(req, timeout=120).read())
        loc, ver = d["localization"], d.get("verification") or {}
        out[fam] = {
            "detection": d["detection"]["verdict"],
            "differing": d["detection"]["differing_vectors"],
            "signature": loc.get("signature"),
            "status": loc.get("status"),
            "sites": [f["site"] for f in (loc.get("faults") or [])],
            "types": loc.get("fault_types"),
            "verify": ver.get("verdict"),
            "p": ver.get("probability"),
        }
    return out


failures: list[str] = []

step(f"installing silently to {TARGET}")
if TARGET.exists():
    print("  target already exists - removing first")
    un = TARGET / "unins000.exe"
    if un.is_file():
        subprocess.run([str(un), "/VERYSILENT", "/SUPPRESSMSGBOXES"], timeout=300)
        time.sleep(6)

proc = subprocess.run(
    [str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
     f"/DIR={TARGET}", "/TASKS=", "/LOG=" + str(BUILD / "install_trace.log")],
    timeout=900)
print(f"  installer exit = {proc.returncode}")
if proc.returncode != 0:
    failures.append(f"installer exited {proc.returncode}")

time.sleep(4)
exe = TARGET / "Faultiva.exe"
print(f"  installed exe exists: {exe.is_file()}")
if not exe.is_file():
    failures.append("Faultiva.exe not installed")
    print("\nFAILURES:", *failures, sep="\n  ")
    raise SystemExit(1)

size_mb = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file()) / 1e6
print(f"  installed size: {size_mb:,.0f} MB")

step("launching the INSTALLED app")
server = subprocess.Popen(
    [str(exe), "--no-window", "--port", str(PORT)],
    cwd=str(TARGET), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{PORT}"
for _ in range(120):
    try:
        urllib.request.urlopen(base + "/", timeout=2).read()
        break
    except Exception:
        time.sleep(0.5)
else:
    failures.append("installed app never served a page")
    server.kill()
    print("\nFAILURES:", *failures, sep="\n  ")
    raise SystemExit(1)
print("  server responding")

try:
    step("diagnosing all four circuits")
    got = diagnose(base)
    for fam, v in sorted(got.items()):
        print(f"  {fam:24s} {v['detection']:15s} {v['status']:7s} "
              f"sites={v['sites']} {v['types']} {v['verify']}")

    reference = json.loads((BUILD / "frozen_api.json").read_text())
    if got == reference:
        print("\n  identical to the frozen build: YES")
    else:
        failures.append("installed app disagrees with the frozen build")
        for k in sorted(set(got) | set(reference)):
            if got.get(k) != reference.get(k):
                print(f"  DIFFERS {k}")
                print(f"    installed {json.dumps(got.get(k), sort_keys=True)}")
                print(f"    frozen    {json.dumps(reference.get(k), sort_keys=True)}")

    step("toolchain endpoint")
    try:
        tc = json.loads(urllib.request.urlopen(
            base + "/api/toolchain", timeout=30).read())
        print(f"  ready={tc.get('ready')} needs_wsl={tc.get('needs_wsl')}")
    except Exception as exc:
        print(f"  not wired yet ({type(exc).__name__}) - expected at this stage")
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    time.sleep(3)

step("uninstalling")
un = TARGET / "unins000.exe"
if not un.is_file():
    failures.append("uninstaller missing")
else:
    r = subprocess.run([str(un), "/VERYSILENT", "/SUPPRESSMSGBOXES"], timeout=600)
    print(f"  uninstaller exit = {r.returncode}")
    time.sleep(8)
    left = list(TARGET.rglob("*")) if TARGET.exists() else []
    print(f"  directory removed: {not TARGET.exists()}")
    if left:
        print(f"  {len(left)} leftover entries, e.g. {[str(p.name) for p in left[:5]]}")
        failures.append(f"{len(left)} files left after uninstall")

print()
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("INSTALL / RUN / DIAGNOSE / UNINSTALL: all clean")
