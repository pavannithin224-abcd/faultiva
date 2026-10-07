"""Command-line entry point: python -m faultiva.characterize --config my.yaml

Characterize your own circuit so Faultiva can diagnose it.

    python -m faultiva.characterize --config my_core.yaml
    python -m faultiva.characterize --config my_core.yaml --check
    python -m faultiva.characterize --config my_core.yaml --workers 4

Characterization is a one-time offline step per circuit: it simulates every
single stuck-at fault against every test vector, which takes minutes to hours
depending on circuit size.  Afterwards diagnosis is a sub-millisecond lookup.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m faultiva.characterize",
        description="Characterize your own circuit for Faultiva.")
    parser.add_argument("--config", required=True, type=Path,
                        help="YAML description of your circuit")
    parser.add_argument("--out", type=Path, default=None,
                        help="catalogue directory (default: <package>/user_circuits)")
    parser.add_argument("--work", type=Path, default=None,
                        help="scratch directory (default: <out>/<circuit>/work)")
    parser.add_argument("--workers", type=int, default=10,
                        help="parallel simulator processes (default 10)")
    parser.add_argument("--yosys", default=None, help="path to yosys")
    parser.add_argument("--verilator", default=None, help="path to verilator")
    parser.add_argument("--check", action="store_true",
                        help="validate the config and stop - no simulation")
    parser.add_argument("--no-resume", action="store_true",
                        help="ignore an existing checkpoint and start over")
    args = parser.parse_args(argv)

    from .config import ConfigError, load_config

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    print(f"circuit        {cfg.circuit}")
    print(f"top module     {cfg.top}")
    print(f"protocol       {cfg.protocol}")
    print(f"rtl files      {len(cfg.rtl)}")
    print(f"clock / reset  {cfg.clock} / {cfg.reset.name}"
          f"{' (active low)' if cfg.reset.active_low else ''}")
    print(f"start / done   {cfg.start.name}"
          f"{' (pulsed)' if cfg.start.pulse else ''} / {cfg.done.name}")
    print(f"inputs         {', '.join(f'{p.name}[{p.width}]' for p in cfg.inputs)}")
    print(f"output         {cfg.output.name}[{cfg.output.width}]")
    print(f"vectors        {cfg.vectors}")
    print(f"cycle budget   {cfg.cycle_budget}")

    if args.check:
        print("\nconfig is valid.  Re-run without --check to characterize.")
        return 0

    out_root = args.out or (PKG_ROOT / "user_circuits")
    print(f"\ncatalogue -> {out_root / cfg.circuit}\n")

    from .campaign import CampaignError, characterize
    try:
        characterize(cfg, out_root, workers=args.workers, work_dir=args.work,
                     yosys=args.yosys, verilator=args.verilator,
                     resume=not args.no_resume)
    except CampaignError as exc:
        print(f"\ncharacterization failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted - progress is checkpointed, re-run to resume",
              file=sys.stderr)
        return 130
    except Exception:                                   # noqa: BLE001
        traceback.print_exc()
        return 1

    print("\nThe circuit will appear in the dashboard circuit list.")
    print("V1 verification stays OUT_OF_SCOPE: it is trained on "
          "opentitan_hmac_sha256 only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
