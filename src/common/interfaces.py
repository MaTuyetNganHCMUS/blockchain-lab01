"""Shared contracts, data models, protocols, and constants agreed by all 5 modules.

This module formalizes the interface agreements for Blockchain Lab 01:
- Core Data Models: Transaction, BlockHeader, BlockBody, Vote, NetPacket, QuorumResult.
- Protocol Constants: NIL_BLOCK_HASH, GENESIS_PARENT_HASH, domains, phases, event types.
- Module Interfaces (typing.Protocol with @runtime_checkable):
    1. CryptoProtocol       (TV1 - Cryptography & Canonical Serialization)
    2. StateProtocol        (TV2 - Execution Engine & State Commitment)
    3. LedgerProtocol       (TV2 - Ledger View & Block Storage)
    4. NetworkProtocol      (TV3 - Discrete-Event Network Simulator)
    5. ConsensusProtocol    (TV4 - Consensus FSM & Validator Node)
    6. VoteTrackerProtocol  (TV5 - Quorum & Vote Counting)
    7. LockManagerProtocol  (TV5 - Locking Rules & PoLC)
    8. LoggerProtocol       (TV5 - Deterministic Event Logging)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ==============================================================================
# PROTOCOL CONSTANTS
# ==============================================================================

NIL_BLOCK_HASH: bytes = b"NIL"
GENESIS_PARENT_HASH: bytes = b"\x00" * 32

# Cryptographic Domain Separation Prefixes
DOMAIN_TX: str = "TX"
DOMAIN_HEADER: str = "HEADER"
DOMAIN_VOTE: str = "VOTE"

# Consensus Phases
PHASE_PREVOTE: str = "PREVOTE"
PHASE_PRECOMMIT: str = "PRECOMMIT"

# Consensus Steps
STEP_NEW_ROUND: str = "NEW_ROUND"
STEP_PROPOSE: str = "PROPOSE"
STEP_PREVOTE: str = "PREVOTE"
STEP_PRECOMMIT: str = "PRECOMMIT"
STEP_COMMIT: str = "COMMIT"

# Network Message Types
MSG_HEADER: str = "HEADER"
MSG_BODY: str = "BODY"
MSG_BODY_REQUEST: str = "BODY_REQUEST"
MSG_VOTE: str = "VOTE"
MSG_TX: str = "TX"

# Event Types for Deterministic Logging
EVENT_SEND: str = "SEND"
EVENT_DROP: str = "DROP"
EVENT_DELAY: str = "DELAY"
EVENT_BLOCK: str = "BLOCK"
EVENT_UNBLOCK: str = "UNBLOCK"
EVENT_PROPOSE: str = "PROPOSE"
EVENT_PREVOTE: str = "PREVOTE"
EVENT_PRECOMMIT: str = "PRECOMMIT"
EVENT_LOCK: str = "LOCK"
EVENT_FINALIZE: str = "FINALIZE"


# ==============================================================================
# 1. CORE DATA STRUCTURES
# ==============================================================================


@dataclass(frozen=True)
class Transaction:
    """Atomic state change request signed by a participant.

    Attributes:
        sender: Identifier of the account/participant proposing the change.
        key: Storage key to update (must be prefixed with f"{sender}/").
        value: New value associated with key.
        nonce: Monotonically increasing replay-protection counter for sender.
        sig: Cryptographic signature under context `TX:<chain_id>`.
    """

    sender: str
    key: str
    value: str
    nonce: int
    sig: bytes = b""


@dataclass(frozen=True)
class BlockHeader:
    """Consensus and cryptographic header linking the blockchain history.

    Attributes:
        height: Positive integer denoting the block index in the chain (genesis=1).
        round: Consensus round in which this block proposal succeeded.
        parent_hash: SHA-256 hash of the parent BlockHeader.
        state_hash: State commitment root after applying transactions in body.
        tx_root: Merkle or canonical commitment hash over transaction list.
        proposer: Identifier of the validator proposing this block.
        sig: Cryptographic signature of proposer under `HEADER:<chain_id>`.
    """

    height: int
    round: int
    parent_hash: bytes
    state_hash: bytes
    tx_root: bytes
    proposer: str
    sig: bytes = b""


@dataclass(frozen=True)
class BlockBody:
    """Ordered collection of transactions encapsulated within a block.

    Attributes:
        header_hash: SHA-256 hash of the associated BlockHeader.
        transactions: Ordered tuple of verified transactions. Normalizes sequence input
            into an immutable tuple to maintain frozen dataclass guarantees.
    """

    header_hash: bytes
    transactions: tuple[Transaction, ...] = field(default_factory=tuple)

    def __init__(
        self,
        header_hash: bytes,
        transactions: Sequence[Transaction] = (),
    ) -> None:
        object.__setattr__(self, "header_hash", header_hash)
        object.__setattr__(self, "transactions", tuple(transactions))


@dataclass(frozen=True)
class Vote:
    """Signed statement from a validator supporting or rejecting a block.

    Attributes:
        height: Block height being decided.
        round: Consensus round within this height.
        phase: Voting phase ("PREVOTE" or "PRECOMMIT").
        block_hash: Hash of the proposed block, or NIL_BLOCK_HASH (b"NIL").
        voter: Identifier of the voting validator.
        sig: Cryptographic signature under context `VOTE:<chain_id>`.
    """

    height: int
    round: int
    phase: str
    block_hash: bytes
    voter: str
    sig: bytes = b""


@dataclass(frozen=True)
class NetPacket:
    """Simulated network wire protocol packet.

    Attributes:
        msg_type: Type identifier ("HEADER", "BODY", "BODY_REQUEST", "VOTE", "TX").
        sender: Node identifier of the sender.
        receiver: Node identifier of the receiver.
        payload: Serialized wire payload bytes.
    """

    msg_type: str
    sender: str
    receiver: str
    payload: bytes


@dataclass(frozen=True)
class QuorumResult:
    """Result returned by VoteTracker indicating whether a 2f + 1 quorum was achieved.

    Attributes:
        has_quorum: True if >= 2f + 1 matching votes observed for this block_hash.
        block_hash: The block hash reaching quorum (or NIL_BLOCK_HASH).
        round: The consensus round of the quorum.
        phase: The voting phase ("PREVOTE" or "PRECOMMIT").
        votes: Tuple of matching Vote objects forming this quorum.
    """

    has_quorum: bool
    block_hash: bytes
    round: int
    phase: str
    votes: tuple[Vote, ...] = field(default_factory=tuple)

    def __init__(
        self,
        has_quorum: bool,
        block_hash: bytes,
        round: int,
        phase: str,
        votes: Sequence[Vote] = (),
    ) -> None:
        object.__setattr__(self, "has_quorum", has_quorum)
        object.__setattr__(self, "block_hash", block_hash)
        object.__setattr__(self, "round", round)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "votes", tuple(votes))


# ==============================================================================
# 2. MODULE API CONTRACTS (TYPING PROTOCOLS)
# ==============================================================================


@runtime_checkable
class CryptoProtocol(Protocol):
    """TV1 Contract: Deterministic canonical encoding, hashing, and Ed25519 signatures."""

    def encode_canonical(self, obj: Any) -> bytes:
        """Deterministic byte serialization across all platforms and environments."""
        ...

    def hash_bytes(self, payload: bytes) -> bytes:
        """Collision-resistant cryptographic hash (e.g., SHA-256)."""
        ...

    def sign(self, priv_key: bytes, domain: str, payload_bytes: bytes) -> bytes:
        """Sign payload under an explicit domain separation string."""
        ...

    def verify(self, pub_key: bytes, domain: str, payload_bytes: bytes, sig: bytes) -> bool:
        """Verify signature validity under the given domain."""
        ...


@runtime_checkable
class StateProtocol(Protocol):
    """TV2 Contract: Key-value state execution, ownership checks, and commitment computation."""

    def validate_and_compute_hash(
        self,
        tx_list: Sequence[Transaction],
    ) -> tuple[bool, bytes]:
        """Validate transaction list (ownership, nonce, signature) and compute candidate state hash.

        Returns:
            (True, post_execution_state_hash) if all valid, or (False, b"") on error.
        """
        ...

    def commit_block(self, block: BlockBody) -> bytes:
        """Permanently apply block transactions to state and return updated state hash."""
        ...


@runtime_checkable
class LedgerProtocol(Protocol):
    """TV2 Contract: Local ledger tracking pending and finalized blocks."""

    def add_finalized_block(self, header: BlockHeader, body: BlockBody) -> None:
        """Append finalized block to local ledger permanently."""
        ...


@runtime_checkable
class NetworkProtocol(Protocol):
    """TV3 Contract: Discrete-event network simulator, header gossip, and timeouts."""

    def send(self, sender: str, receiver: str, raw_bytes: bytes) -> None:
        """Enqueue point-to-point packet into simulated network queue."""
        ...

    def broadcast_header(self, sender: str, header_bytes: bytes) -> None:
        """Broadcast block header to all connected peers in network."""
        ...

    def schedule_timeout(self, node_id: str, delay: float | int, callback_tag: str) -> None:
        """Register discrete-event timer callback on the simulation timeline."""
        ...


@runtime_checkable
class ConsensusProtocol(Protocol):
    """TV4 Contract: BFT Consensus Finite State Machine event handlers."""

    def on_packet(
        self,
        sim_time: float | int,
        packet_bytes: bytes,
        sender: str = "",
    ) -> None:
        """Process incoming network packet at current simulation time."""
        ...

    def on_timeout(
        self,
        sim_time: float | int,
        height: int,
        round: int,
        step: str,
    ) -> None:
        """Handle step/round timeout event triggered by network simulator."""
        ...


@runtime_checkable
class VoteTrackerProtocol(Protocol):
    """TV5 Contract: 2f + 1 quorum tracking and duplicate vote filtering (Rule 1, 2, 7)."""

    def add_vote(self, vote: Vote) -> QuorumResult:
        """Record a vote and evaluate whether a 2f + 1 quorum is satisfied."""
        ...


@runtime_checkable
class LockManagerProtocol(Protocol):
    """TV5 Contract: Consensus locking rules and Proof-of-Lock-Change (Rule 4, 5)."""

    def can_prevote(self, block_hash: bytes, round: int, polka_round: int) -> bool:
        """Evaluate if validator can prevote for block_hash under locking & PoLC rules."""
        ...


@runtime_checkable
class LoggerProtocol(Protocol):
    """TV5 Contract: Deterministic structured event logging."""

    def log(
        self,
        sim_time: float | int,
        node_id: str,
        event_type: str,
        height: int,
        round: int,
        detail: str,
    ) -> None:
        """Record an event with deterministic timestamp, node_id, and metadata."""
        ...


# ==============================================================================
# 3. PROTOCOL UTILITY FUNCTIONS
# ==============================================================================


def select_proposer(height: int, round: int, validators: Sequence[str]) -> str:
    r"""Deterministic proposer selection function:

    $$\text{proposer} = \text{validators}[(\text{height} + \text{round}) \pmod n]$$
    """
    if not validators:
        raise ValueError("Validator set cannot be empty.")
    index = (height + round) % len(validators)
    return validators[index]
