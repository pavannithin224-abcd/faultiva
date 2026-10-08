#!/usr/bin/env python3
"""Detection prefers the pinned OSS CAD Suite.

This exists because two separate code paths both got it wrong: the native
probe consulted PATH before the suite, and the WSL probe looked for the suite
in a shell variable that arrives empty, so both silently returned a
distribution package instead of the pinned build.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faultiva import toolchain as tc

PINNED_YOSYS = "0.68"
PINNED_VERILATOR = "5.051"

fails = []


def ck(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        fails.append(label)


print("=== candidate ordering ===")
home = "/home/example"
for name in ("yosys", "verilator"):
    cands = tc._wsl_candidates(name, home)
    first_suite = next((i for i, p in enumerate(cands) if "oss-cad-suite" in p), -1)
    first_usr = next((i for i, p in enumerate(cands) if p.startswith("/usr/")), -1)
    ck(f"{name}: suite before /usr", 0 <= first_suite < first_usr,
       f"suite@{first_suite} usr@{first_usr}")
    ck(f"{name}: no shell variables",
       not any("$" in p for p in cands))

cands = tc._wsl_candidates("yosys", None)
ck("survives unknown home", all("$" not in p for p in cands) and cands)

print()
print("=== native probe order ===")
src = Path(tc.__file__).read_text(encoding="utf-8")
body = src[src.index("def _probe_local"):src.index("def _wsl_ok")]
i_suite = body.find("_SUITE_HINTS")
i_path = body.find("shutil.which")
ck("suite consulted before PATH", 0 < i_suite < i_path,
   f"suite@{i_suite} path@{i_path}")

print()
print("=== detection on this machine ===")
chain = tc.detect()
print(f"  yosys     {chain.yosys.path}  {chain.yosys.version}  via {chain.yosys.via}")
print(f"  verilator {chain.verilator.path}  {chain.verilator.version}  via {chain.verilator.via}")

if chain.yosys.found and chain.verilator.found:
    suite_present = Path("~/tools/oss-cad-suite/bin/yosys").expanduser().is_file()
    if suite_present:
        ck("yosys is the pinned build",
           PINNED_YOSYS in (chain.yosys.version or ""), chain.yosys.version or "")
        ck("verilator is the pinned build",
           PINNED_VERILATOR in (chain.verilator.version or ""),
           chain.verilator.version or "")
    else:
        print("  (pinned suite not installed here; version check skipped)")
else:
    print("  (toolchain not available here; version check skipped)")

print()
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("detection prefers the pinned toolchain")
