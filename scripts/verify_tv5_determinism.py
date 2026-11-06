"""Run Test T8: two identical seeds must produce byte-identical TV5 logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.security.scenarios import run_equivocation_scenario


def verify_determinism(seed: int, output_dir: Path) -> dict[str, Any]:
    """Run T7/T8, persist both logs, and write a deterministic JSON summary."""

    output_dir.mkdir(parents=True, exist_ok=True)
    first = run_equivocation_scenario(seed=seed, log_path=output_dir / "run-1.log")
    second = run_equivocation_scenario(seed=seed, log_path=output_dir / "run-2.log")
    log_bytes_match = (output_dir / "run-1.log").read_bytes() == (
        output_dir / "run-2.log"
    ).read_bytes()
    t7_passed = len(first.finalized_blocks) == 1 and first.finalized_blocks == (
        second.finalized_blocks
    )
    t8_passed = (
        log_bytes_match
        and first.log_hash == second.log_hash
        and first.final_state_hash == second.final_state_hash
        and first.finalized_blocks == second.finalized_blocks
    )
    report: dict[str, Any] = {
        "configuration": {
            "byzantine_count": 1,
            "seed": seed,
            "validator_count": 4,
        },
        "final_state_hash": first.final_state_hash.hex(),
        "finalized_blocks": [block_hash.hex() for block_hash in first.finalized_blocks],
        "log_hashes": {
            "run_1": first.log_hash,
            "run_2": second.log_hash,
        },
        "tests": {
            "T7_byzantine_equivocation": "PASS" if t7_passed else "FAIL",
            "T8_deterministic_execution": "PASS" if t8_passed else "FAIL",
        },
    }
    summary = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (output_dir / "results.json").write_text(summary, encoding="utf-8", newline="\n")
    return report


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
