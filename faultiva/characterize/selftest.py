#!/usr/bin/env python3
"""Validate the ported netlist functions against frozen V2.2 evidence.

The ported enumerate_sites() must reproduce the exact site catalogue the V2.2
campaign used.  The authoritative reference is `site_catalog_sha256` in the
frozen 12C-1M eligibility summary, computed over every site as:

    sha256( opaque_site_id \0 cell_name \0 output_port \0
            output_bit_index \0 net_bit_id \n  ... for every site in rank order )

A matching digest proves identical enumeration order, identical cell/port/bit
selection and identical opaque-id derivation - not merely a matching count.

Exits non-zero on any mismatch.  Run from the Faultiva repo root with the
project venv active.
"""
from __future__ import annotations

import csv
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The frozen V2.2 evidence lives in the CircuitSage V2 study tree, which is a
# separate repository and NOT shipped with Faultiva.  Point FAULTIVA_V2_TREE at
# it to run this validation; without it the self-test skips cleanly.
V2 = Path(os.environ.get("FAULTIVA_V2_TREE",
                         Path.home() / "vlsi_fault_detection_v2"))
R12C1 = V2 / "results/circuitsage_hmac_v2_12c1"
SUMMARY = (R12C1 / "site_eligibility_discovery_12c1m"
           / "circuitsage_hmac_v2_2_site_eligibility_summary_12c1m.csv")
SYNTH = R12C1 / "train_calibration_synthesis_12c1e/families"

sys.path.insert(0, str(REPO))
from faultiva.characterize import enumerate_sites, instrument_netlist, site_summary  # noqa: E402

# family order fixes the opaque-id prefix S<n>-, so it must match the campaign
FAMILY_ORDER = ("opentitan_hmac_sha256", "picorv32_cpu",
                "secworks_aes", "secworks_sha256")


def catalog_digest(sites: list[dict]) -> str:
    """Reproduce 12C-1M's confirm_enumerator_equivalence() digest exactly."""
    h = hashlib.sha256()
    for s in sites:
        h.update(f"{s['opaque_site_id']}\0{s['cell_name']}\0"
                 f"{s['output_port']}\0{s['output_bit_index']}\0"
                 f"{s['net_bit_id']}\n".encode())
    return h.hexdigest()


def netlist_path(family: str) -> Path:
    return SYNTH / family / f"{family}_generic_12c1e.json"


def main() -> int:
    if not SUMMARY.is_file():
        print(f"SKIP: frozen summary not found at {SUMMARY}")
        print("      (this self-test requires the V2 project tree)")
        return 0

    with open(SUMMARY, newline="", encoding="utf-8") as h:
        frozen = {r["family_id"]: r for r in csv.DictReader(h)}

    print("=" * 76)
    print("FAULTIVA CHARACTERIZATION SELF-TEST")
    print("ported enumerate_sites() vs frozen 12C-1M site_catalog_sha256")
    print("=" * 76)

    failures = 0
    for number, family in enumerate(FAMILY_ORDER):
        row = frozen.get(family)
        nl = netlist_path(family)
        print(f"\n[{family}]")
        if row is None:
            print("  SKIP  not in frozen summary")
            continue
        if not nl.is_file():
            print(f"  SKIP  netlist missing: {nl.name}")
            continue

        top = row["top_module"]
        expect_count = int(row["eligible_sites_12c1m"])
        expect_digest = row["site_catalog_sha256"]

        sites = enumerate_sites(family, nl, top, family_number=number)
        got_digest = catalog_digest(sites)
        ranks_ok = [s["site_rank"] for s in sites] == list(range(len(sites)))

        print(f"  top module       {top}")
        print(f"  sites   ported   {len(sites):,}")
        print(f"          frozen   {expect_count:,}")
        print(f"  rank continuity  {'OK' if ranks_ok else 'BROKEN'}")
        print(f"  digest  ported   {got_digest}")
        print(f"          frozen   {expect_digest}")

        ok = (len(sites) == expect_count and got_digest == expect_digest
              and ranks_ok)
        if ok:
            print("  RESULT           MATCH - byte-identical site catalogue")
        else:
            print("  RESULT           MISMATCH")
            failures += 1
            continue

        # instrumentation must also be deterministic and complete
        payload = instrument_netlist(family, nl, top, sites)
        again = instrument_netlist(family, nl, top, sites)
        det = hashlib.sha256(payload).hexdigest()
        if payload != again:
            print("  instrument       NON-DETERMINISTIC")
            failures += 1
        else:
            summary = site_summary(sites)
            print(f"  instrumented     {len(payload):,} bytes  sha={det[:24]}...")
            print(f"  faults           {summary['faults']:,}  "
                  f"({summary['sequential_sites']:,} seq / "
                  f"{summary['combinational_sites']:,} comb, "
                  f"{summary['distinct_cell_types']} cell types)")

    print("\n" + "=" * 76)
    if failures:
        print(f"RESULT: FAIL  -  {failures} circuit(s) did not reproduce frozen evidence")
        return 1
    print("RESULT: PASS  -  all circuits reproduce the frozen site catalogue exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
