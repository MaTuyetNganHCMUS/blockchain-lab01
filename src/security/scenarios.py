"""Small self-contained TV5 scenarios used by E2E tests T7 and T8."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path

from src.common.interfaces import EVENT_FINALIZE, EVENT_PREVOTE, PHASE_PREVOTE, Vote
from src.observability.logger import DeterministicLogger

from .byzantine import ByzantineAttackSimulator
from .locking import VoteTracker


@dataclass(frozen=True)
class ScenarioResult:
    finalized_blocks: tuple[bytes, ...]
    final_state_hash: bytes
    log_hash: str


def run_equivocation_scenario(
    *,
    seed: int = 42,
    log_path: str | Path | None = None,
) -> ScenarioResult:
    """Run a deterministic 4-validator scenario with one equivocator.

    The two observers see different votes from node0, while all three honest
    validators vote for block A. Both views must therefore finalize only A.
    """

    validators = ("node0", "node1", "node2", "node3")
    block_a = hashlib.sha256(b"block-A").digest()
    block_b = hashlib.sha256(b"block-B").digest()
    attack = ByzantineAttackSimulator(validators, ("node0",))
    base_vote = Vote(1, 0, PHASE_PREVOTE, block_a, "node0")
    directed = attack.equivocate("node0", base_vote, block_b)
    by_receiver = {delivery.receiver: delivery.vote for delivery in directed}

    logger = DeterministicLogger(log_path)
    rng = random.Random(seed)
    finalized: list[bytes] = []
    for observer in ("node1", "node3"):
        tracker = VoteTracker(validators, expected_height=1)
        votes = [by_receiver[observer]]
        votes.extend(Vote(1, 0, PHASE_PREVOTE, block_a, voter) for voter in validators[1:])
        rng.shuffle(votes)

        decision: bytes | None = None
        for sequence, vote in enumerate(votes):
            result = tracker.add_vote(vote)
            logger.log(
                sequence / 100,
                observer,
                EVENT_PREVOTE,
                vote.height,
                vote.round,
                {"block": vote.block_hash.hex(), "voter": vote.voter},
            )
            if result.has_quorum:
                decision = result.block_hash
        if decision is not None:
            finalized.append(decision)
            logger.log(1, observer, EVENT_FINALIZE, 1, 0, {"block": decision.hex()})

    if log_path is not None:
        logger.write()
    unique_finalized = tuple(sorted(set(finalized)))
    final_state_hash = hashlib.sha256(b"state:" + block_a).digest()
    return ScenarioResult(unique_finalized, final_state_hash, logger.digest())


__all__ = ["ScenarioResult", "run_equivocation_scenario"]
