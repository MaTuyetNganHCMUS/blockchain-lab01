"""Run Test T8: two identical seeds must produce byte-identical TV5 logs."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.security.scenarios import run_equivocation_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("logs/t8"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    first = run_equivocation_scenario(seed=args.seed, log_path=args.output_dir / "run-1.log")
    second = run_equivocation_scenario(seed=args.seed, log_path=args.output_dir / "run-2.log")
    matches = (
        first.log_hash == second.log_hash
        and first.final_state_hash == second.final_state_hash
        and first.finalized_blocks == second.finalized_blocks
    )
    print(f"run-1 log sha256: {first.log_hash}")
    print(f"run-2 log sha256: {second.log_hash}")
    print(f"final_state_hash: {first.final_state_hash.hex()}")
    print(f"T8 deterministic: {'PASS' if matches else 'FAIL'}")
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
