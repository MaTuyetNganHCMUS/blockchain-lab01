"""Command-line entry point for the Blockchain Lab 01 simulator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .simulation import (
    SUPPORTED_SCENARIOS,
    load_simulation_config,
    run_scenario,
    summary_to_dict,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="Run deterministic Blockchain Lab 01 BFT consensus simulator scenarios.",
    )
    parser.add_argument(
        "--scenario",
        default="normal",
        choices=[*SUPPORTED_SCENARIOS, "all"],
        help="Simulation scenario to run (default: normal).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/default.json"),
        help="Path to simulator JSON configuration (default: config/default.json).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional PRNG seed override.",
    )
    parser.add_argument(
        "--validators",
        type=int,
        default=None,
        help="Optional validator count override (must satisfy n = 3f + 1, n >= 4).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output directory or file path for deterministic logs.",
    )
    args = parser.parse_args(argv)

    config_path = args.config if args.config.exists() else None
    try:
        cfg = load_simulation_config(
            config_path,
            seed_override=args.seed,
            validator_count_override=args.validators,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    scenarios_to_run = list(SUPPORTED_SCENARIOS) if args.scenario == "all" else [args.scenario]
    summaries = []
    for name in scenarios_to_run:
        out_dir = (args.output / name) if (args.output is not None and len(scenarios_to_run) > 1) else args.output
        summary = run_scenario(name, cfg, output_path=out_dir)
        summaries.append(summary_to_dict(summary))

    payload = summaries if len(summaries) > 1 else summaries[0]
    print(json.dumps(payload, indent=2, sort_keys=True))
    all_passed = all(item["passed"] for item in summaries)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
