"""BFT vote tracking and the seven locking/validation rules used by Lab 01.

The classes in this module depend only on the shared contracts, so they can be
used by the consensus FSM before the other implementation branches are merged.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.common.interfaces import (
    DOMAIN_VOTE,
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    QuorumResult,
    Vote,
)

if TYPE_CHECKING:
    from src.common.interfaces import CryptoProtocol


from src.common.serializer import encode_canonical


VoteStep = tuple[int, int, str]


def vote_signing_bytes(vote: Vote) -> bytes:
    """Return a deterministic canonical signature payload for an unsigned vote."""
    return encode_canonical((
        vote.block_hash,
        vote.height,
        vote.phase,
        vote.round,
        vote.voter,
    ))


@dataclass(frozen=True)
class RejectedVote:
    """Audit record for a vote that was not admitted to a quorum tally."""

    vote: Vote
    reason: str


@dataclass(frozen=True)
class EquivocationEvidence:
    """Two conflicting votes signed by one validator for the same step."""

    first: Vote
    conflicting: Vote


class VoteTracker:
    """Validate, de-duplicate, and tally votes into ``2f + 1`` quorums.

    Rules enforced here:

    * Rules 1/2: one PREVOTE and one PRECOMMIT per validator and (H, R).
    * Rule 6: known validator, valid coordinates/phase/hash, optional signature
      verification under ``VOTE:<chain_id>`` and an optional expected height.
    * Rule 7: exact duplicates are idempotent; conflicting duplicates are retained
      as equivocation evidence but never counted.
    """

    def __init__(
        self,
        validators: Sequence[str] | None = None,
        *,
        validator_count: int | None = None,
        quorum_threshold: int | None = None,
        chain_id: str = "lab01",
        expected_height: int | None = None,
        crypto: CryptoProtocol | None = None,
        public_keys: Mapping[str, bytes] | None = None,
        require_signatures: bool | None = None,
    ) -> None:
        if validators is None:
            count = 4 if validator_count is None else validator_count
            if count <= 0:
                raise ValueError("validator_count must be positive")
            validators = tuple(f"node{i}" for i in range(count))
        elif validator_count is not None and validator_count != len(validators):
            raise ValueError("validator_count does not match validators")

        normalized = tuple(validators)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("validators must be non-empty and unique")
        if not chain_id:
            raise ValueError("chain_id must not be empty")
        if expected_height is not None and expected_height <= 0:
            raise ValueError("expected_height must be positive")

        self.validators = normalized
        self._validator_set = frozenset(normalized)
        self.validator_count = len(normalized)
        self.f = (self.validator_count - 1) // 3
        self.quorum = quorum_threshold if quorum_threshold is not None else 2 * self.f + 1
        if not 1 <= self.quorum <= self.validator_count:
            raise ValueError("quorum_threshold must be within the validator set")

        self.chain_id = chain_id
        self.expected_height = expected_height
        self.crypto = crypto
        self.public_keys = dict(public_keys or {})
        self.require_signatures = crypto is not None if require_signatures is None else require_signatures
        if self.require_signatures and self.crypto is None:
            raise ValueError("crypto is required when require_signatures=True")

        self._votes: dict[VoteStep, dict[str, Vote]] = defaultdict(dict)
        self._equivocations: list[EquivocationEvidence] = []
        self._rejected: list[RejectedVote] = []
        self._duplicate_count = 0

    @property
    def vote_domain(self) -> str:
        return f"{DOMAIN_VOTE}:{self.chain_id}"

    @property
    def equivocations(self) -> tuple[EquivocationEvidence, ...]:
        return tuple(self._equivocations)

    @property
    def rejected_votes(self) -> tuple[RejectedVote, ...]:
        return tuple(self._rejected)

    @property
    def duplicate_count(self) -> int:
        return self._duplicate_count

    @property
    def last_rejection(self) -> str | None:
        return self._rejected[-1].reason if self._rejected else None

    def set_expected_height(self, height: int) -> None:
        if height <= 0:
            raise ValueError("height must be positive")
        self.expected_height = height

    def validate_vote(self, vote: Vote) -> tuple[bool, str]:
        """Validate Rule 6 without changing any tally state."""

        if vote.voter not in self._validator_set:
            return False, "unknown validator"
        if vote.height <= 0:
            return False, "height must be positive"
        if self.expected_height is not None and vote.height != self.expected_height:
            return False, "unexpected height"
        if vote.round < 0:
            return False, "round must be non-negative"
        if vote.phase not in (PHASE_PREVOTE, PHASE_PRECOMMIT):
            return False, "invalid phase"
        if not isinstance(vote.block_hash, bytes) or not vote.block_hash:
            return False, "block_hash must be non-empty bytes"

        if self.require_signatures:
            public_key = self.public_keys.get(vote.voter)
            if public_key is None:
                return False, "missing validator public key"
            assert self.crypto is not None
            if not self.crypto.verify(
                public_key,
                self.vote_domain,
                vote_signing_bytes(vote),
                vote.sig,
            ):
                return False, "invalid vote signature or domain"
        return True, ""

    def add_vote(self, vote: Vote) -> QuorumResult:
        """Admit one vote and return the quorum status for its block hash."""

        valid, reason = self.validate_vote(vote)
        if not valid:
            self._rejected.append(RejectedVote(vote, reason))
            return self._result(vote, ())

        step = (vote.height, vote.round, vote.phase)
        previous = self._votes[step].get(vote.voter)
        if previous is not None:
            if previous == vote:
                self._duplicate_count += 1
            else:
                evidence = EquivocationEvidence(previous, vote)
                if evidence not in self._equivocations:
                    self._equivocations.append(evidence)
                self._rejected.append(RejectedVote(vote, "equivocation"))
            matching = self.get_votes(vote.height, vote.round, vote.phase, vote.block_hash)
            return self._result(vote, matching)

        self._votes[step][vote.voter] = vote
        matching = self.get_votes(vote.height, vote.round, vote.phase, vote.block_hash)
        return self._result(vote, matching)

    def _result(self, vote: Vote, votes: Sequence[Vote]) -> QuorumResult:
        ordered = tuple(sorted(votes, key=lambda item: item.voter))
        return QuorumResult(
            has_quorum=len(ordered) >= self.quorum,
            block_hash=vote.block_hash,
            round=vote.round,
            phase=vote.phase,
            votes=ordered,
        )

    def get_votes(
        self,
        height: int,
        round: int,
        phase: str,
        block_hash: bytes | None = None,
    ) -> tuple[Vote, ...]:
        votes = self._votes.get((height, round, phase), {}).values()
        if block_hash is not None:
            votes = (vote for vote in votes if vote.block_hash == block_hash)
        return tuple(sorted(votes, key=lambda item: item.voter))

    def has_equivocated(self, voter: str, height: int, round: int, phase: str) -> bool:
        return any(
            evidence.first.voter == voter
            and evidence.first.height == height
            and evidence.first.round == round
            and evidence.first.phase == phase
            for evidence in self._equivocations
        )


class LockManager:
    """Maintain validator locks and validate local signing decisions.

    Rule 3 requires a PREVOTE quorum before a non-NIL PRECOMMIT. Rule 4 records
    that quorum as a lock. Rule 5 permits changing a lock only with a PoLC from
    a strictly newer, earlier round.
    """

    def __init__(self, *, quorum_threshold: int = 1, require_recorded_polka: bool = False) -> None:
        if quorum_threshold <= 0:
            raise ValueError("quorum_threshold must be positive")
        self.quorum_threshold = quorum_threshold
        self.require_recorded_polka = require_recorded_polka
        self.locked_block: bytes | None = None
        self.locked_round = -1
        self.locked_height: int | None = None
        self.current_height: int | None = None
        self._local_votes: dict[VoteStep, Vote] = {}
        self._polkas: dict[tuple[int | None, int, bytes], tuple[Vote, ...]] = {}

    def start_height(self, height: int) -> None:
        if height <= 0:
            raise ValueError("height must be positive")
        if self.current_height != height:
            self.current_height = height
            self.unlock()

    def can_sign(self, vote: Vote) -> bool:
        """Rules 1/2: a validator may sign at most once for this vote step."""

        return (vote.height, vote.round, vote.phase) not in self._local_votes

    def record_local_vote(self, vote: Vote) -> bool:
        if not self.can_sign(vote):
            return False
        self._local_votes[(vote.height, vote.round, vote.phase)] = vote
        return True

    def observe_quorum(self, result: QuorumResult) -> bool:
        """Validate a PREVOTE certificate, remember its PoLC, and apply Rule 4."""

        if not result.has_quorum or result.phase != PHASE_PREVOTE:
            return False
        votes = tuple(result.votes)
        if len(votes) < self.quorum_threshold or len({vote.voter for vote in votes}) != len(votes):
            return False
        if not votes:
            return False
        first = votes[0]
        if any(
            vote.height != first.height
            or vote.round != result.round
            or vote.phase != PHASE_PREVOTE
            or vote.block_hash != result.block_hash
            for vote in votes
        ):
            return False

        key = (first.height, result.round, result.block_hash)
        self._polkas[key] = tuple(sorted(votes, key=lambda vote: vote.voter))
        if result.block_hash != NIL_BLOCK_HASH:
            self.record_lock(result.block_hash, result.round, height=first.height)
        return True

    def record_polka(
        self,
        block_hash: bytes,
        round: int,
        *,
        height: int | None = None,
        votes: Sequence[Vote] = (),
    ) -> None:
        """Record already-validated PoLC evidence supplied by the consensus layer."""

        if round < 0 or block_hash == NIL_BLOCK_HASH:
            raise ValueError("a non-NIL polka requires a non-negative round")
        target_height = height if height is not None else self.current_height
        self._polkas[(target_height, round, block_hash)] = tuple(votes)

    def record_lock(self, block_hash: bytes, round: int, *, height: int | None = None) -> None:
        if block_hash == NIL_BLOCK_HASH or not block_hash:
            raise ValueError("cannot lock NIL or an empty block hash")
        if round < 0:
            raise ValueError("lock round must be non-negative")
        target_height = height if height is not None else self.current_height
        if self.locked_block is None or round >= self.locked_round:
            self.locked_block = block_hash
            self.locked_round = round
            self.locked_height = target_height

    def can_prevote(self, block_hash: bytes, round: int, polka_round: int = -1) -> bool:
        """Apply Rules 4/5 to a proposed PREVOTE decision."""

        if round < 0 or not block_hash:
            return False
        if block_hash == NIL_BLOCK_HASH:
            return True
        if self.locked_block is None or block_hash == self.locked_block:
            return True
        if not self.locked_round < polka_round < round:
            return False
        if not self.require_recorded_polka:
            return True
        key = (self.current_height, polka_round, block_hash)
        return key in self._polkas

    def can_precommit(self, block_hash: bytes, round: int, *, height: int | None = None) -> bool:
        """Rule 3: non-NIL PRECOMMIT requires a matching PREVOTE quorum."""

        if round < 0:
            return False
        target_height = height if height is not None else self.current_height
        if block_hash == NIL_BLOCK_HASH:
            return True
        return (target_height, round, block_hash) in self._polkas

    def unlock(self) -> None:
        self.locked_block = None
        self.locked_round = -1
        self.locked_height = None


__all__ = [
    "EquivocationEvidence",
    "LockManager",
    "RejectedVote",
    "VoteTracker",
    "vote_signing_bytes",
]
