"""Does a circuit characterized mid-session appear without a restart?

The first attempt copied a catalogue directory and expected a new entry, but
the manifest still named the original circuit, so it loaded under the same
name and nothing could appear.  The copy now gets its own name, which is what
a real campaign produces.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path("/home/pavan/faultiva_repo")
sys.path.insert(0, str(REPO))
from faultiva import Faultiva

ROOT = Path(Faultiva.user_circuit_roots()[0])
SRC = ROOT / "siphash_core"
NAME = "rescan_probe"
TMP = ROOT / NAME
PORT = 7899
BASE = f"http://127.0.0.1:{PORT}"

if not SRC.is_dir():
    sys.exit(f"need an existing catalogue at {SRC}")
if TMP.exists():
    shutil.rmtree(TMP)


def circuits():
    with urllib.request.urlopen(BASE + "/api/circuits", timeout=60) as r:
        d = json.load(r)
    items = d if isinstance(d, list) else d.get("circuits", [])
    return sorted(c.get("id") or c.get("circuit") or "" for c in items)


proc = subprocess.Popen(
    [sys.executable, "app.py", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(REPO / "app"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = False
try:
    for _ in range(60):
        time.sleep(1)
        try:
            before = circuits()
            break
        except Exception:
            continue
    else:
        sys.exit("server never came up")

    print("=== before ===")
    for c in before:
        print("  " + c)
    assert NAME not in before, "probe name already present"

    print()
    print("=== a campaign finishes while the app is running ===")
    shutil.copytree(SRC, TMP)
    manifest = json.loads((TMP / "manifest.json").read_text(encoding="utf-8"))
    manifest["circuit"] = NAME                    # as a real campaign would
    (TMP / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
    print("  wrote", TMP.name, "with circuit =", manifest["circuit"])
    time.sleep(1.5)

    after = circuits()
    print()
    print("=== after, same running server (no restart) ===")
    for c in after:
        print("  " + c)

    added = sorted(set(after) - set(before))
    print()
    print("appeared without a restart:", added)

    if NAME in after:
        # listed is not enough: it has to be usable
        with urllib.request.urlopen(
                BASE + f"/api/example?circuit={NAME}", timeout=120) as r:
            ex = json.load(r)
        text = ex.get("text") or ex.get("content") or ""
        print("example capture lines:", len(text.splitlines()))
        ok = bool(text.strip())
    else:
        print("still missing from the list")

    print()
    print("=== and removing it is noticed too ===")
    shutil.rmtree(TMP)
    time.sleep(1.5)
    final = circuits()
    gone = NAME not in final
    print("  removed from the list:", gone)
    ok = ok and gone
finally:
    proc.kill()
    if TMP.exists():
        shutil.rmtree(TMP)

print()
print("PASS - a new circuit shows up live" if ok
      else "FAIL - still needs a restart")
sys.exit(0 if ok else 1)
