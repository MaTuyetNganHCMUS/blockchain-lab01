"""TV5 end-to-end scenarios T7 (safety) and T8 (determinism)."""

import json

from scripts.verify_tv5_determinism import verify_determinism
from src.security.scenarios import run_equivocation_scenario


def test_t7_equivocation_does_not_finalize_two_forks(tmp_path) -> None:
    result = run_equivocation_scenario(seed=42, log_path=tmp_path / "t7.log")
    assert len(result.finalized_blocks) == 1


def test_t8_same_seed_has_identical_log_and_final_state(tmp_path) -> None:
    first = run_equivocation_scenario(seed=42, log_path=tmp_path / "run-1.log")
    second = run_equivocation_scenario(seed=42, log_path=tmp_path / "run-2.log")

    assert first.log_hash == second.log_hash
    assert first.final_state_hash == second.final_state_hash
    assert first.finalized_blocks == second.finalized_blocks
    assert (tmp_path / "run-1.log").read_bytes() == (tmp_path / "run-2.log").read_bytes()


def test_result_summary_captures_t7_t8_evidence(tmp_path) -> None:
    report = verify_determinism(42, tmp_path)
    persisted = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))

    assert report == persisted
    assert persisted["tests"] == {
        "T7_byzantine_equivocation": "PASS",
        "T8_deterministic_execution": "PASS",
    }
    assert persisted["log_hashes"]["run_1"] == persisted["log_hashes"]["run_2"]
