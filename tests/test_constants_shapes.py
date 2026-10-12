#!/usr/bin/env python3
"""Does the constants normaliser accept what the page actually sends?

The failure was a width-less entry reaching the schema.  Check the real shapes:
the page's own output, an explicit width, a width that is too small, and the
old mapping form that must keep working.
"""
import sys

sys.path.insert(0, "/home/pavan/faultiva_repo/app")
sys.path.insert(0, "/home/pavan/faultiva_repo")

from characterize_api import _normalise_constants as norm

fails = []


def ck(label, fn, want=None, should_raise=False):
    try:
        got = fn()
    except Exception as exc:
        if should_raise:
            print(f"  PASS  {label} -> rejected: {str(exc)[:88]}")
        else:
            print(f"  FAIL  {label} -> {type(exc).__name__}: {str(exc)[:88]}")
            fails.append(label)
        return
    if should_raise:
        print(f"  FAIL  {label} -> accepted {got}, expected rejection")
        fails.append(label)
    elif want is not None and got != want:
        print(f"  FAIL  {label}")
        print(f"          got  {got}")
        print(f"          want {want}")
        fails.append(label)
    else:
        print(f"  PASS  {label} -> {got}")


print("=== exactly what the page sent in the screenshot ===")
ck("one-bit pin, no width",
   lambda: norm([{"name": "initalize", "value": "0"}]),
   [{"name": "initalize", "width": 1, "value": 0}])

print()
print("=== the full SipHash set, widths supplied by the page ===")
ck("six pins",
   lambda: norm([
       {"name": "initalize", "value": "0"},
       {"name": "compress", "value": "0"},
       {"name": "long", "value": "1"},
       {"name": "compression_rounds", "value": "2", "width": 4},
       {"name": "final_rounds", "value": "4", "width": 4},
       {"name": "mi", "value": "0", "width": 64}]),
   [{"name": "initalize", "width": 1, "value": 0},
    {"name": "compress", "width": 1, "value": 0},
    {"name": "long", "width": 1, "value": 1},
    {"name": "compression_rounds", "width": 4, "value": 2},
    {"name": "final_rounds", "width": 4, "value": 4},
    {"name": "mi", "width": 64, "value": 0}])

print()
print("=== a value that needs more bits than the pin has ===")
ck("4 in a 2-bit pin", lambda: norm(
    [{"name": "compression_rounds", "value": "4", "width": 2}]),
   should_raise=True)

print()
print("=== width inferred when the value needs several bits ===")
ck("value 4, no width",
   lambda: norm([{"name": "rounds", "value": "4"}]),
   [{"name": "rounds", "width": 3, "value": 4}])

print()
print("=== junk is refused clearly ===")
ck("non-numeric value", lambda: norm([{"name": "x", "value": "two"}]),
   should_raise=True)
ck("missing name", lambda: norm([{"value": "1"}]), should_raise=True)

print()
print("=== the mapping form still works ===")
ck("mapping", lambda: norm({"mode": 1}),
   [{"name": "mode", "width": 1, "value": 1}])
ck("[width, value] pair", lambda: norm({"rounds": [4, 2]}),
   [{"name": "rounds", "width": 4, "value": 2}])

print()
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("constants are accepted in every shape the page produces")
