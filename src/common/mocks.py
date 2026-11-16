"""Mock and Stub implementations for all 5 modules in Blockchain Lab 01.

These mocks strictly adhere to the protocols defined in `src.common.interfaces`,
enabling each team member (TV1 to TV5) to test and run their modules independently
prior to end-to-end integration:

    - MockCrypto: Deterministic canonical encoder, SHA-256 hasher, HMAC signer/verifier.
    - MockState: In-memory KV state engine with ownership enforcement and deterministic commitment.
    - MockLedger: In-memory block ledger tracking finalized headers and bodies.
    - MockNetwork: In-memory discrete-event packet recorder and timer registry.
    - MockConsensus: In-memory event receiver for network packets and round timeouts.
    - MockVoteTracker: 2f + 1 quorum aggregator and duplicate voter deduplicator (Rule 7).
    - MockLockManager: Locking and Proof-of-Lock-Change (PoLC) evaluator (Rule 4 & 5).
    - MockLogger: Structured event recorder producing deterministic timestamped traces.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from collections.abc import Sequence
from typing import Any

from .interfaces import (
    BlockBody,
    BlockHeader,
    NetPacket,
    QuorumResult,
    Transaction,
    Vote,
)
from .serializer import encode_canonical as encode_shared_canonical

# ==============================================================================
# TV1 MOCK: CRYPTO & CANONICAL SERIALIZER
# ==============================================================================


class MockCrypto:
    """Mock implementation of CryptoProtocol using SHA-256 and HMAC."""

    def __init__(self) -> None:
        self._key_registry: dict[bytes, bytes] = {}  # pub_key -> priv_key

    def register_keypair(self, priv_key: bytes, pub_key: bytes) -> None:
        """Register a known private/public key pair in mock registry."""
        self._key_registry[pub_key] = priv_key

    def generate_keypair(self, identity: str) -> tuple[bytes, bytes]:
        """Generate a deterministic mock keypair for a given identity string."""
        priv_key = hashlib.sha256(f"priv:{identity}".encode("utf-8")).digest()
        pub_key = hashlib.sha256(f"pub:{identity}".encode("utf-8")).digest()
        self.register_keypair(priv_key, pub_key)
        return priv_key, pub_key

    def hash_bytes(self, payload: bytes) -> bytes:
        """Compute SHA-256 digest of raw bytes."""
        return hashlib.sha256(payload).digest()

    def encode_canonical(self, obj: Any) -> bytes:
        """Use the production canonical encoder in mock integrations."""
        return encode_shared_canonical(obj)

    def sign(self, priv_key: bytes, domain: str, payload_bytes: bytes) -> bytes:
        """Compute HMAC-SHA256 signature with explicit domain separation."""
        context = f"{domain}:".encode("utf-8")
        return hmac.new(priv_key, context + payload_bytes, hashlib.sha256).digest()

    def verify(self, pub_key: bytes, domain: str, payload_bytes: bytes, sig: bytes) -> bool:
        """Verify signature against the registered private key or matching pub_key."""
        if not sig:
            return False
        priv_key = self._key_registry.get(pub_key)
        if priv_key is not None:
            expected = self.sign(priv_key, domain, payload_bytes)
            return hmac.compare_digest(sig, expected)
        # Fallback: if pub_key itself was used directly as HMAC key in a test
        expected_fallback = self.sign(pub_key, domain, payload_bytes)
        return hmac.compare_digest(sig, expected_fallback)


# ==============================================================================
# TV2 MOCK: STATE ENGINE & LEDGER
# ==============================================================================


class MockState:
    """Mock implementation of StateProtocol for key-value state execution."""

    def __init__(self, initial_values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(initial_values) if initial_values else {}
        self.nonces: dict[str, int] = {}
        self.executed_tx_hashes: set[bytes] = set()

    def compute_state_hash(self, state_dict: dict[str, str] | None = None) -> bytes:
        """Compute deterministic state hash by hashing sorted key-value pairs."""
        target = self.values if state_dict is None else state_dict
        sorted_pairs = sorted(target.items())
        raw_data = "\n".join(f"{k}={v}" for k, v in sorted_pairs).encode("utf-8")
        return hashlib.sha256(raw_data).digest()

    def validate_and_compute_hash(
        self,
        tx_list: Sequence[Transaction],
    ) -> tuple[bool, bytes]:
        """Validate transaction list and compute the resulting candidate state hash.

        Enforces:
        - Ownership rule: sender 'Alice' can only update keys starting with 'Alice/'.
        - Replay protection: nonce must be monotonically greater than current sender nonce.
        """
        temp_state = copy.deepcopy(self.values)
        temp_nonces = copy.deepcopy(self.nonces)

        for tx in tx_list:
            expected_prefix = f"{tx.sender}/"
            if not tx.key.startswith(expected_prefix):
                return False, b""

            last_nonce = temp_nonces.get(tx.sender, -1)
            if tx.nonce <= last_nonce:
                return False, b""

            temp_nonces[tx.sender] = tx.nonce
            temp_state[tx.key] = tx.value

        return True, self.compute_state_hash(temp_state)

    def commit_block(self, block: BlockBody) -> bytes:
        """Apply block transactions permanently and return updated state hash."""
        for tx in block.transactions:
            self.values[tx.key] = tx.value
            self.nonces[tx.sender] = tx.nonce
        return self.compute_state_hash(self.values)


class MockLedger:
    """Mock implementation of LedgerProtocol tracking finalized blocks."""

    def __init__(self) -> None:
        self.headers: list[BlockHeader] = []
        self.bodies: list[BlockBody] = []
        self.by_height: dict[int, tuple[BlockHeader, BlockBody]] = {}
        self.by_hash: dict[bytes, tuple[BlockHeader, BlockBody]] = {}

    def add_finalized_block(self, header: BlockHeader, body: BlockBody) -> None:
        """Permanently record a finalized block into ledger."""
        self.headers.append(header)
        self.bodies.append(body)
        self.by_height[header.height] = (header, body)
        self.by_hash[body.header_hash] = (header, body)

    def get_by_height(self, height: int) -> tuple[BlockHeader, BlockBody] | None:
        """Retrieve block at specified height."""
        return self.by_height.get(height)

    def is_finalized(self, height: int, header_hash: bytes) -> bool:
        """Check whether a block hash is finalized at the given height."""
        entry = self.by_height.get(height)
        if entry is None:
            return False
        return entry[1].header_hash == header_hash


# ==============================================================================
# TV3 MOCK: NETWORK SIMULATOR
# ==============================================================================


class MockNetwork:
    """Mock implementation of NetworkProtocol for queuing messages and timers."""

    def __init__(self) -> None:
        self.sent_packets: list[NetPacket] = []
        self.broadcast_headers: list[tuple[str, bytes]] = []
        self.scheduled_timeouts: list[tuple[str, float | int, str]] = []

    def send(self, sender: str, receiver: str, raw_bytes: bytes) -> None:
        """Record outbound point-to-point packet."""
        packet = NetPacket(
            msg_type="RAW",
            sender=sender,
            receiver=receiver,
            payload=raw_bytes,
        )
        self.sent_packets.append(packet)

    def broadcast_header(self, sender: str, header_bytes: bytes) -> None:
        """Record broadcast header transmission."""
        self.broadcast_headers.append((sender, header_bytes))

    def schedule_timeout(self, node_id: str, delay: float | int, callback_tag: str) -> None:
        """Record scheduled timeout callback."""
        self.scheduled_timeouts.append((node_id, delay, callback_tag))

    def clear(self) -> None:
        """Clear all queues."""
        self.sent_packets.clear()
        self.broadcast_headers.clear()
        self.scheduled_timeouts.clear()


# ==============================================================================
# TV4 MOCK: CONSENSUS ENGINE
# ==============================================================================


class MockConsensus:
    """Mock implementation of ConsensusProtocol for recording FSM events."""

    def __init__(self) -> None:
        self.received_packets: list[tuple[float | int, bytes]] = []
        self.received_timeouts: list[tuple[float | int, int, int, str]] = []

    def on_packet(
        self,
        sim_time: float | int,
        packet_bytes: bytes,
        sender: str = "",
    ) -> None:
        """Record received packet at simulation timestamp."""
        self.received_packets.append((sim_time, packet_bytes))

    def on_timeout(
        self,
        sim_time: float | int,
        height: int,
        round: int,
        step: str,
    ) -> None:
        """Record received timeout event."""
        self.received_timeouts.append((sim_time, height, round, step))

    def clear(self) -> None:
        """Clear recorded events."""
        self.received_packets.clear()
        self.received_timeouts.clear()


# ==============================================================================
# TV5 MOCK: VOTE TRACKER, LOCK MANAGER & LOGGER
# ==============================================================================


class MockVoteTracker:
    """Mock implementation of VoteTrackerProtocol enforcing quorum and Rule 7."""

    def __init__(
        self,
        validator_count: int = 4,
        quorum_threshold: int | None = None,
    ) -> None:
        self.validator_count = validator_count
        if quorum_threshold is not None:
            self.quorum = quorum_threshold
        else:
            # Standard BFT: n = 3f + 1 => quorum = 2f + 1
            f = max(0, (validator_count - 1) // 3)
            self.quorum = (2 * f) + 1 if validator_count >= 4 else 1

        # Key: (height, round, phase) -> {voter: Vote}
        self.votes_by_step: dict[tuple[int, int, str], dict[str, Vote]] = {}

    def add_vote(self, vote: Vote) -> QuorumResult:
        """Add a vote and evaluate whether 2f + 1 quorum is satisfied.

        Enforces Rule 7: duplicate votes from the same voter are ignored.
        """
        step_key = (vote.height, vote.round, vote.phase)
        if step_key not in self.votes_by_step:
            self.votes_by_step[step_key] = {}

        voters_map = self.votes_by_step[step_key]
        if vote.voter not in voters_map:
            voters_map[vote.voter] = vote

        # Tally votes grouped by block_hash
        hash_tally: dict[bytes, list[Vote]] = {}
        for v in voters_map.values():
            hash_tally.setdefault(v.block_hash, []).append(v)

        # Check if any block hash reached quorum threshold
        for b_hash, v_list in hash_tally.items():
            if len(v_list) >= self.quorum:
                return QuorumResult(
                    has_quorum=True,
                    block_hash=b_hash,
                    round=vote.round,
                    phase=vote.phase,
                    votes=tuple(v_list),
                )

        # Quorum not reached yet
        return QuorumResult(
            has_quorum=False,
            block_hash=vote.block_hash,
            round=vote.round,
            phase=vote.phase,
            votes=tuple(voters_map.values()),
        )


class MockLockManager:
    """Mock implementation of LockManagerProtocol implementing Rule 4 & 5."""

    def __init__(self) -> None:
        self.locked_block: bytes | None = None
        self.locked_round: int = -1

    def can_prevote(self, block_hash: bytes, round: int, polka_round: int) -> bool:
        """Evaluate whether validator can prevote under locking rules.

        - Rule 4: If not locked, validator can prevote any valid block.
        - Rule 5: If locked on B, validator can prevote B' only if polka_round > locked_round.
        """
        if self.locked_block is None:
            return True
        if block_hash == self.locked_block:
            return True
        if polka_round > self.locked_round:
            return True
        return False

    def record_lock(self, block_hash: bytes, round: int) -> None:
        """Lock on block_hash at round R (Rule 4)."""
        self.locked_block = block_hash
        self.locked_round = round

    def unlock(self) -> None:
        """Clear locked state."""
        self.locked_block = None
        self.locked_round = -1


class MockLogger:
    """Mock implementation of LoggerProtocol for deterministic event capture."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def log(
        self,
        sim_time: float | int,
        node_id: str,
        event_type: str,
        height: int,
        round: int,
        detail: str,
    ) -> None:
        """Record an event with deterministic structure."""
        self.records.append(
            {
                "sim_time": float(sim_time),
                "node_id": node_id,
                "event_type": event_type,
                "height": height,
                "round": round,
                "detail": detail,
            }
        )

    def formatted_lines(self) -> list[str]:
        """Produce formatted log strings matching deterministic evaluation format."""
        return [
            f"[{r['sim_time']:08.2f}] node={r['node_id']} event={r['event_type']} "
            f"H={r['height']} R={r['round']} detail={r['detail']}"
            for r in self.records
        ]
