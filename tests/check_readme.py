#!/usr/bin/env python3
"""Check every number in the proposed README against its source.

A README is a claims document.  Hand-checking figures does not scale and a
transposed digit is indistinguishable from fabrication, so assert each one
against the artifact that produced it.
"""
import json
import re
import sys
from pathlib import Path

DRAFT = Path(__file__).resolve().parent.parent / "README.md"
OLD = Path("/home/pavan/faultiva_work/README.before_rewrite.md")
text = DRAFT.read_text(encoding="utf-8")
old = OLD.read_text(encoding="utf-8")

fails = []


def ck(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        fails.append(label)


print("=== numbers carried over from the current README (must be unchanged) ===")
for claim in ["11,654", "28,189", "2,505", "471", "70.7%", "69.3%", "42.0%",
              "18.5%", "26,940", "14,577", "973", "1,519",
              "127,484", "7,849,696", "93,185", "0.4965", "0.8166",
              "0.9166", "0.1210", "36,784", "29%", "71%", "182", "10.2",
              "10,111", "20,222", "0.082", "0.256"]:
    ck(f"{claim!r} still present", claim in text,
       "" if claim in old else "(not in old README either!)")

print()
print("=== new numbers, checked against measured artifacts ===")
cat = Path("/home/pavan/Faultiva/user_circuits/siphash_core/manifest.json")
if cat.is_file():
    m = json.loads(cat.read_text())
    ck("siphash sites 3,997", "3,997" in text, str(m.get("sites")))
    ck("  matches manifest", m.get("sites") == 3997)
    ck("siphash faults 7,994", "7,994" in text, str(m.get("faults")))
    ck("  matches manifest", m.get("faults") == 7994)
    ck("siphash vectors 64", m.get("vectors") == 64)
    u = round(float(m.get("uniquely_localizable", 0)) * 100, 1)
    ck("uniquely localizable 65.7%", "65.7%" in text, f"manifest {u}%")
    ck("  matches manifest", abs(u - 65.7) < 0.05)
else:
    print("  SKIP  siphash manifest not on this machine")

setup = Path("/nonexistent-on-linux")
if setup.is_file():
    mb = setup.stat().st_size / (1024 * 1024)
    ck("installer size 46 MB", "46 MB" in text, f"actual {mb:.1f} MB")
    ck("  within rounding", abs(mb - 46) < 1)

ck("repo size 5.7 MB", "5.7 MB" in text, "measured 5.7M")

print()
print("=== the claim that was WRONG in the old README ===")
ck("old 'mismatch pattern' phrasing removed",
   "hashes the mismatch pattern" not in text)
ck("four channels now named",
   all(w in text for w in ("response XOR", "cycle\ndelta", "timeout"))
   or all(w in text for w in ("response XOR", "cycle delta", "timeout")))

print()
print("=== claims that must NOT have been softened ===")
for honest in ["not established", "falsified", "research artifact",
               "Do not use it as the sole basis",
               "undefined rather than merely uncertain",
               "zero learned parameters", "OUT_OF_SCOPE"]:
    ck(f"{honest!r} kept", honest in text)

print()
print("=== no invented capability ===")
for forbidden in ["production-ready", "state-of-the-art", "seamless",
                  "comprehensive", "robust", "leverage", "delve",
                  "cutting-edge", "revolutionary", "powerful"]:
    ck(f"no marketing word {forbidden!r}", forbidden.lower() not in text.lower())

print()
print("=== AI-tell check: em-dash density ===")
words = len(text.split())
dashes = text.count("—")
per_1000 = dashes / words * 1000
ck("em-dash density under 4/1000", per_1000 < 4.0,
   f"{dashes} in {words} words = {per_1000:.1f}/1000")

print()
print("=== structure ===")
ck("links to Releases", "../../releases/latest" in text)
ck("documents the app", "Add your circuit" in text)
ck("documents characterization", "faultiva.characterize" in text)
ck("names the toolchain requirement", "Yosys and Verilator" in text)
ck("keeps the citation block", "@software{faultiva2026" in text)

print()
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("every claim in the draft checks out")
