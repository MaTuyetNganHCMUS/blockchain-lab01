"""Run Test T8: two identical seeds must produce byte-identical TV5 logs."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.security.determinism import verify_determinism


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("logs/t8"))
    args = parser.parse_args()

    report = verify_determinism(args.seed, args.output_dir)
    print(f"run-1 log sha256: {report['log_hashes']['run_1']}")
    print(f"run-2 log sha256: {report['log_hashes']['run_2']}")
    print(f"final_state_hash: {report['final_state_hash']}")
    print(f"T7 equivocation safety: {report['tests']['T7_byzantine_equivocation']}")
    print(f"T8 deterministic: {report['tests']['T8_deterministic_execution']}")
    return 0 if all(result == "PASS" for result in report["tests"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
