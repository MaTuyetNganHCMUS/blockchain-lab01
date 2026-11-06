"""Unit tests for TV5 vote, lock, Byzantine, and logging components."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.common.interfaces import (
    EVENT_PREVOTE,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    BlockHeader,
    Vote,
)
from src.common.mocks import MockCrypto
from src.observability.logger import DeterministicLogger
from src.security.byzantine import ByzantineAttackSimulator
from src.security.locking import LockManager, VoteTracker, vote_signing_bytes

VALIDATORS = ("node0", "node1", "node2", "node3")
BLOCK_A = b"A" * 32
BLOCK_B = b"B" * 32


def make_vote(voter: str, block_hash: bytes = BLOCK_A, *, round: int = 0) -> Vote:
    return Vote(1, round, PHASE_PREVOTE, block_hash, voter)


def reach_quorum(tracker: VoteTracker, block_hash: bytes, *, round: int = 0):
    result = None
    for voter in VALIDATORS[:3]:
        result = tracker.add_vote(make_vote(voter, block_hash, round=round))
    assert result is not None
    return result


def test_ten_duplicate_votes_count_once() -> None:
    tracker = VoteTracker(VALIDATORS)
    vote = make_vote("node0")
    for _ in range(10):
        result = tracker.add_vote(vote)

    assert result.has_quorum is False
    assert len(tracker.get_votes(1, 0, PHASE_PREVOTE)) == 1
    assert tracker.duplicate_count == 9


def test_equivocation_is_evidence_but_never_a_second_vote() -> None:
    tracker = VoteTracker(VALIDATORS)
    tracker.add_vote(make_vote("node0", BLOCK_A))
    conflicting = tracker.add_vote(make_vote("node0", BLOCK_B))

    assert conflicting.has_quorum is False
    assert tracker.has_equivocated("node0", 1, 0, PHASE_PREVOTE)
    assert len(tracker.equivocations) == 1
    assert tracker.get_votes(1, 0, PHASE_PREVOTE, BLOCK_B) == ()


@pytest.mark.parametrize(
    ("vote", "reason"),
    [
        (Vote(2, 0, PHASE_PREVOTE, BLOCK_A, "node0"), "unexpected height"),
        (Vote(1, -1, PHASE_PREVOTE, BLOCK_A, "node0"), "round"),
        (Vote(1, 0, "INVALID", BLOCK_A, "node0"), "phase"),
        (Vote(1, 0, PHASE_PREVOTE, BLOCK_A, "outsider"), "unknown validator"),
    ],
)
def test_rejects_wrong_vote_coordinates(vote: Vote, reason: str) -> None:
    tracker = VoteTracker(VALIDATORS, expected_height=1)
    result = tracker.add_vote(vote)
    assert result.has_quorum is False
    assert reason in (tracker.last_rejection or "")


def test_signature_uses_chain_specific_vote_domain() -> None:
    crypto = MockCrypto()
    private_key, public_key = crypto.generate_keypair("node0")
    unsigned = make_vote("node0")
    signature = crypto.sign(private_key, "VOTE:lab01", vote_signing_bytes(unsigned))
    signed = replace(unsigned, sig=signature)
    tracker = VoteTracker(
        VALIDATORS,
        crypto=crypto,
        public_keys={"node0": public_key},
        require_signatures=True,
    )

    assert tracker.add_vote(signed).has_quorum is False
    wrong_chain = VoteTracker(
        VALIDATORS,
        chain_id="another-chain",
        crypto=crypto,
        public_keys={"node0": public_key},
        require_signatures=True,
    )
    wrong_chain.add_vote(signed)
    assert wrong_chain.last_rejection == "invalid vote signature or domain"


def test_prevote_and_precommit_are_independent_vote_slots() -> None:
    tracker = VoteTracker(VALIDATORS)
    prevote = make_vote("node0")
    precommit = replace(prevote, phase=PHASE_PRECOMMIT)
    tracker.add_vote(prevote)
    tracker.add_vote(precommit)
    assert len(tracker.get_votes(1, 0, PHASE_PREVOTE)) == 1
    assert len(tracker.get_votes(1, 0, PHASE_PRECOMMIT)) == 1


def test_lock_rule_and_unlock_only_with_newer_polka() -> None:
    tracker = VoteTracker(VALIDATORS)
    manager = LockManager(quorum_threshold=tracker.quorum, require_recorded_polka=True)
    manager.start_height(1)
    polka_a = reach_quorum(tracker, BLOCK_A, round=0)
    assert manager.observe_quorum(polka_a)
    assert manager.locked_block == BLOCK_A
    assert manager.can_prevote(BLOCK_B, round=1, polka_round=-1) is False
    assert manager.can_prevote(BLOCK_B, round=2, polka_round=1) is False

    tracker_round_1 = VoteTracker(VALIDATORS)
    polka_b = reach_quorum(tracker_round_1, BLOCK_B, round=1)
    assert manager.observe_quorum(polka_b)
    assert manager.locked_block == BLOCK_B
    assert manager.can_prevote(BLOCK_B, round=2, polka_round=1) is True


def test_precommit_requires_matching_polka() -> None:
    tracker = VoteTracker(VALIDATORS)
    manager = LockManager(quorum_threshold=tracker.quorum)
    manager.start_height(1)
    assert manager.can_precommit(BLOCK_A, 0) is False
    assert manager.observe_quorum(reach_quorum(tracker, BLOCK_A))
    assert manager.can_precommit(BLOCK_A, 0) is True
    assert manager.can_precommit(BLOCK_B, 0) is False


def test_local_validator_cannot_sign_twice_per_phase() -> None:
    manager = LockManager()
    first = make_vote("node1")
    assert manager.record_local_vote(first) is True
    assert manager.record_local_vote(first) is False
    assert manager.record_local_vote(replace(first, block_hash=BLOCK_B)) is False
    assert manager.record_local_vote(replace(first, phase=PHASE_PRECOMMIT)) is True


def test_byzantine_attacks_are_bounded_and_deterministic() -> None:
    simulator = ByzantineAttackSimulator(VALIDATORS, ("node0",))
    deliveries = simulator.equivocate("node0", make_vote("node0"), BLOCK_B)
    assert [item.receiver for item in deliveries] == ["node1", "node2", "node3"]
    assert {item.vote.block_hash for item in deliveries} == {BLOCK_A, BLOCK_B}
    assert len(simulator.replay("node0", b"old", copies=10).payloads) == 10
    assert simulator.silence("node0") == ()

    header = BlockHeader(1, 0, b"parent", b"state", b"tx", "node0")
    invalid = simulator.invalid_state_proposal("node0", header)
    assert invalid.state_hash != header.state_hash
    with pytest.raises(ValueError, match="exceeds"):
        ByzantineAttackSimulator(VALIDATORS, ("node0", "node1"))


def test_deterministic_logger_normalizes_details_and_bytes(tmp_path) -> None:
    first = DeterministicLogger(tmp_path / "first.log")
    second = DeterministicLogger(tmp_path / "second.log")
    for logger in (first, second):
        logger.log(0.125, "node1", EVENT_PREVOTE, 1, 0, {"z": 2, "a": 1})
        logger.log(1, "node2", EVENT_PREVOTE, 1, 0, "line1\nline2")
        logger.write()

    assert first.to_bytes() == second.to_bytes()
    assert first.digest() == second.digest()
    assert first.formatted_lines()[0].endswith('detail={"a":1,"z":2}')
    assert b"line1\\nline2" in first.to_bytes()
