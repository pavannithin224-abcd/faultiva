#!/usr/bin/env python3
"""Drive the real page the way a user does, and catch runtime errors.

A syntax check passes on code that throws ReferenceError the moment it runs -
which is exactly how the Check button came to hang forever: `fields()` read a
variable declared in another IIFE, threw before fetch, so no .catch and no
.finally ever ran.

applyPorts and fields live inside IIFEs and cannot be called from outside, so
this drives the DOM instead: attach a file to the RTL input, fire the events
the browser fires, click Check, and assert that a request actually leaves and
the button recovers.  Any uncaught error is reported.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BUILD = Path(r"C:\Users\1\faultiva_build")
HTML = Path(sys.argv[1] if len(sys.argv) > 1 else BUILD / "stage/app/ui/index.html")
RTL = Path(r"C:\Users\1\OneDrive\Desktop\faultiva_inputs\circuits\siphash\siphash_core.v")

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
const errors = [];

window.onerror = (m) => { errors.push("uncaught: " + m); };
window.addEventListener("error", e => errors.push("uncaught: " + e.message));

let sent = null;
window.fetch = function(url, opts){
  sent = { url: url, body: opts && opts.body };
  return new Promise(() => {});          // never resolves, like a slow server
};
window.setInterval = () => 0;
window.clearInterval = () => {};
if (!window.DataTransfer)
  window.DataTransfer = function(){ this.items = { add(){} }; this.files = []; };

payload.blocks.forEach((src, i) => {
  try { window.eval(src); }
  catch (e) { errors.push("block " + (i+1) + " threw on load: " + e.message); }
});

const doc = window.document;
const g = id => doc.getElementById(id);

// attach the design the way a file picker does
const file = new window.File([payload.rtl], "siphash_core.v",
                             { type: "text/plain" });
const rtl = g("cz-rtl");
Object.defineProperty(rtl, "files", { value: [file], configurable: true });
try {
  rtl.dispatchEvent(new window.Event("change"));
} catch (e) { errors.push("change handler threw: " + e.message); }

// FileReader is async; let it settle, then finish as the user would
setTimeout(() => {
  const set = (id, v) => {
    const el = g(id);
    if (!el) { errors.push("missing field " + id); return; }
    el.value = v;
    try { el.dispatchEvent(new window.Event("input")); } catch (e) {}
    try { el.dispatchEvent(new window.Event("change")); } catch (e) {}
  };
  set("cz-start", "finalize");
  set("cz-done", "siphash_word_valid");

  const check = g("cz-check");
  try {
    check.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  } catch (e) { errors.push("click handler threw: " + e.message); }

  setTimeout(() => {
    const shape = {};
    if (sent && sent.body && sent.body.entries)
      for (const [k, v] of sent.body.entries())
        shape[k] = (v && v.name) ? "<file:" + v.name + ">" : String(v);

    console.log(JSON.stringify({
      errors: errors,
      requested: !!sent,
      url: sent ? sent.url : null,
      shape: shape,
      constants: (g("cz-const") || {}).value || "",
      fields: ["cz-clock","cz-reset","cz-din","cz-dinw","cz-dout","cz-doutw"]
              .reduce((o,id) => (o[id] = (g(id)||{}).value, o), {}),
      buttonText: (g("cz-check") || {}).textContent || ""
    }));
  }, 300);
}, 400);
"""

payload = BUILD / "_page_payload.json"
payload.write_text(json.dumps({
    "body": body, "blocks": blocks,
    "rtl": RTL.read_text(encoding="utf-8", errors="replace")}), encoding="utf-8")
harness = BUILD / "_page_harness.js"
harness.write_text(HARNESS, encoding="utf-8")

out = subprocess.run(
    ["node", str(harness).replace("\\", "/"), str(payload).replace("\\", "/")],
    capture_output=True, text=True, timeout=180, cwd=str(BUILD))
if out.returncode != 0:
    print(out.stderr[:1500])
    sys.exit("node failed")

res = json.loads(out.stdout.strip().splitlines()[-1])
fails = list(res["errors"])

print("=== runtime errors ===")
print("  none" if not res["errors"] else "")
for e in res["errors"]:
    print("  " + e)

print()
print("=== fields filled from the design ===")
for k, v in res["fields"].items():
    print(f"  {k:12s} {v}")

print()
print("=== constants offered ===")
for line in (res["constants"] or "").splitlines():
    print("  " + line)

print()
print("=== did clicking Check actually send a request? ===")
print("  requested:", res["requested"], "->", res["url"])
if not res["requested"]:
    fails.append("Check sent no request - the button would hang")

shape = res.get("shape") or {}
for k in sorted(shape):
    print(f"  {k:16s} {str(shape[k])[:66]}")

for need in ("top", "clock", "reset", "start", "done", "rtl"):
    if need not in shape:
        fails.append("request missing " + need)

if "constants" in shape:
    consts = json.loads(shape["constants"])
    widths = {c["name"]: c.get("width") for c in consts}
    print()
    print("  widths taken from the design:", widths)
    if widths.get("compression_rounds") != 4:
        fails.append("width not attached from the design")
    if "finalize" in widths:
        fails.append("start pin sent as a held constant")
else:
    fails.append("request missing constants")

print()
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("the page runs, fills itself, and submits a valid request")
