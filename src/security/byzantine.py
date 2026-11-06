"""Deterministic Byzantine attack primitives for consensus simulations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Generic, TypeVar

from src.common.interfaces import BlockHeader, Vote


class ByzantineBehavior(str, Enum):
    EQUIVOCATION = "EQUIVOCATION"
    INVALID_STATE_HASH = "INVALID_STATE_HASH"
    REPLAY = "REPLAY"
    SILENCE = "SILENCE"


@dataclass(frozen=True)
class DirectedVote:
    """One Byzantine vote delivered to one network recipient."""

    sender: str
    receiver: str
    vote: Vote


T = TypeVar("T")


@dataclass(frozen=True)
class ReplayBatch(Generic[T]):
    """The repeated payloads produced by a replay attack."""

    sender: str
    payloads: tuple[T, ...]


class ByzantineAttackSimulator:
    """Create reproducible faults while enforcing the configured ``f`` bound."""

    def __init__(
        self,
        validators: Sequence[str],
        byzantine_nodes: Sequence[str],
        *,
        sign_vote: Callable[[Vote], bytes] | None = None,
    ) -> None:
        self.validators = tuple(validators)
        self.byzantine_nodes = frozenset(byzantine_nodes)
        if not self.validators or len(set(self.validators)) != len(self.validators):
            raise ValueError("validators must be non-empty and unique")
        if not self.byzantine_nodes.issubset(self.validators):
            raise ValueError("Byzantine nodes must belong to the validator set")
        self.max_faults = (len(self.validators) - 1) // 3
        if len(self.byzantine_nodes) > self.max_faults:
            raise ValueError("Byzantine node count exceeds f=(n-1)//3")
        self._sign_vote = sign_vote

    def equivocate(
        self,
        voter: str,
        vote: Vote,
        conflicting_block_hash: bytes,
        recipients: Sequence[str] | None = None,
    ) -> tuple[DirectedVote, ...]:
        """Send conflicting votes to deterministic halves of the recipient set."""

        self._require_byzantine(voter)
        if vote.voter != voter:
            raise ValueError("vote.voter must match the Byzantine sender")
        if not conflicting_block_hash or conflicting_block_hash == vote.block_hash:
            raise ValueError("equivocation requires a different non-empty block hash")

        targets = sorted(set(recipients or self.validators) - {voter})
        if len(targets) < 2:
            raise ValueError("equivocation requires at least two recipients")
        split_at = (len(targets) + 1) // 2
        conflicting = replace(vote, block_hash=conflicting_block_hash, sig=b"")
        conflicting = self._resign(conflicting)
        original = self._resign(replace(vote, sig=b"")) if self._sign_vote else vote

        deliveries = [DirectedVote(voter, target, original) for target in targets[:split_at]]
        deliveries.extend(
            DirectedVote(voter, target, conflicting) for target in targets[split_at:]
        )
        return tuple(deliveries)

    def invalid_state_proposal(self, proposer: str, header: BlockHeader) -> BlockHeader:
        """Return a proposal with a deterministic, deliberately wrong state hash."""

        self._require_byzantine(proposer)
        if header.proposer != proposer:
            raise ValueError("header.proposer must match the Byzantine proposer")
        invalid_hash = hashlib.sha256(b"INVALID_STATE_HASH\x00" + header.state_hash).digest()
        if invalid_hash == header.state_hash:  # defensive, practically impossible
            invalid_hash = bytes(value ^ 0xFF for value in invalid_hash)
        return replace(header, state_hash=invalid_hash, sig=b"")

    def replay(self, sender: str, payload: T, *, copies: int = 10) -> ReplayBatch[T]:
        """Repeat an old packet/vote exactly; no mutation hides de-duplication bugs."""

        self._require_byzantine(sender)
        if copies <= 0:
            raise ValueError("copies must be positive")
        return ReplayBatch(sender=sender, payloads=(payload,) * copies)

    def silence(self, sender: str) -> tuple[()]:
        """Represent a crash/withholding strategy by producing no outbound messages."""

        self._require_byzantine(sender)
        return ()

    def _resign(self, vote: Vote) -> Vote:
        if self._sign_vote is None:
            return vote
        return replace(vote, sig=self._sign_vote(vote))

    def _require_byzantine(self, node_id: str) -> None:
        if node_id not in self.byzantine_nodes:
            raise ValueError(f"{node_id!r} is not configured as Byzantine")


# Short alias used by some integration code.
ByzantineSimulator = ByzantineAttackSimulator


__all__ = [
    "ByzantineAttackSimulator",
    "ByzantineBehavior",
    "ByzantineSimulator",
    "DirectedVote",
    "ReplayBatch",
]
