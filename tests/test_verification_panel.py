#!/usr/bin/env python3
"""Render all three verification verdicts and check what each one says.

The panel only runs when a result arrives, so neither a syntax check nor the
page-load test exercises it.  Drive the real renderer with each verdict and
assert the text a user would read.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BUILD = Path(r"C:\Users\1\faultiva_build")
HTML = Path(sys.argv[1] if len(sys.argv) > 1 else BUILD / "stage/app/ui/index.html")

html = HTML.read_text(encoding="utf-8")
blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
body = re.sub(r"<script>.*?</script>", "",
              re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1), flags=re.S)

HARNESS = r"""
const { JSDOM } = require("jsdom");
const fs = require("fs");
const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const dom = new JSDOM(
  "<!DOCTYPE html><html><body>" + payload.body + "</body></html>",
  { runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
window.fetch = () => new Promise(() => {});
window.setInterval = () => 0; window.clearInterval = () => {};
if (!window.DataTransfer)
  window.DataTransfer = function(){ this.items = { add(){} }; this.files = []; };

const errors = [];
payload.blocks.forEach((src, i) => {
  try { window.eval(src); }
  catch (e) { errors.push("block " + (i+1) + ": " + e.message); }
});

const out = {};
payload.cases.forEach(c => {
  const target = window.document.getElementById("out");
  if (target) target.innerHTML = "";
  try {
    window.eval("render(" + JSON.stringify(c.data) + ")");
    out[c.name] = target ? target.textContent.replace(/\s+/g, " ").trim() : "";
  } catch (e) {
    errors.push(c.name + " threw: " + e.message);
    out[c.name] = null;
  }
});
console.log(JSON.stringify({ errors, out }));
"""


def result(verdict, family, fault_count, extra=None):
    ver = {"verdict": verdict}
    if extra:
        ver.update(extra)
    return {
        "circuit": family,
        "detection": {"verdict": "FAULT_DETECTED", "vectors_differing": 38,
                      "vectors": 64},
        "localization": {"status": "EXACT", "fault_count": fault_count,
                         "signature": "a1b2c3",
                         "fault_types": ["SA0"], "type_certain": True,
                         "faults": [{"site": 174, "type": "SA0",
                                     "stuck": 0, "net_name": "u_core.x",
                                     "cell_type": "$_XOR_"}]},
        "verification": ver,
    }


cases = [
    {"name": "out_of_scope",
     "data": result("OUT_OF_SCOPE", "siphash_core", 1,
                    {"reason": "V1 was trained only on opentitan_hmac_sha256"})},
    {"name": "out_of_scope_many",
     "data": result("OUT_OF_SCOPE", "siphash_core", 47,
                    {"reason": "V1 was trained only on opentitan_hmac_sha256"})},
    {"name": "verified",
     "data": result("VERIFIED", "opentitan_hmac_sha256", 1,
                    {"sites_scored": 1, "above_threshold": 1,
                     "threshold": 0.4965,
                     "note": "independent structural evidence agrees"})},
    {"name": "flagged",
     "data": result("FLAGGED", "opentitan_hmac_sha256", 10,
                    {"sites_scored": 10, "above_threshold": 0,
                     "threshold": 0.4965, "note": "no corroboration"})},
]

payload = BUILD / "_verif_payload.json"
payload.write_text(json.dumps({"body": body, "blocks": blocks, "cases": cases}),
                   encoding="utf-8")
harness = BUILD / "_verif_harness.js"
harness.write_text(HARNESS, encoding="utf-8")

proc = subprocess.run(
    ["node", str(harness).replace("\\", "/"), str(payload).replace("\\", "/")],
    capture_output=True, text=True, timeout=180, cwd=str(BUILD))
if proc.returncode != 0:
    print(proc.stderr[:1200])
    sys.exit("node failed")

res = json.loads(proc.stdout.strip().splitlines()[-1])
fails = list(res["errors"])
for e in res["errors"]:
    print("  ERROR " + e)

text = res["out"]


def ck(case, must, must_not=()):
    t = text.get(case) or ""
    print(f"\n=== {case} ===")
    i = t.find("Verification")
    print("  " + (t[i:i + 300] if i >= 0 else t[:200]))
    for phrase in must:
        if phrase.lower() not in t.lower():
            print(f"  MISSING: {phrase!r}")
            fails.append(f"{case}: missing {phrase!r}")
    for phrase in must_not:
        if phrase.lower() in t.lower():
            print(f"  SHOULD NOT APPEAR: {phrase!r}")
            fails.append(f"{case}: contains {phrase!r}")


ck("out_of_scope",
   ["NOT APPLICABLE", "Detection and localization above are unaffected",
    "1 candidate", "opentitan_hmac_sha256"],
   ["OUT OF SCOPE", "1 candidates"])
ck("out_of_scope_many", ["47 candidates"], ["47 candidate:"])
ck("verified", ["VERIFIED", "sites scored", "0.4965"],
   ["Detection and localization above are unaffected"])
ck("flagged", ["FLAGGED", "29%", "not a failed localization"])

print()
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("every verdict renders the right explanation")
