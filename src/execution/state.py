"""Key-Value State Engine with Ownership Validation & Replay Protection.
Owner: TV2 (Execution Engine & State Commitment)
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..common.interfaces import DOMAIN_TX, BlockBody, BlockHeader, Transaction
from ..crypto.ed25519 import verify
from .commitment import compute_state_commitment, compute_tx_hash, tx_signing_payload


class StateEngine:
    """Deterministic Key-Value State Engine satisfying StateProtocol.

    Enforces:
    0. Cryptographic Signature Verification: TX signed under TX:<chain_id> by registered participant.
    1. Prefix-based Access Control: Sender 'X' can only write to key 'X/...'.
    2. Replay Protection: Sequential nonces and executed tx hash tracking.
    3. Dry-run Isolation: Ability to validate tx lists without mutating base state.
    4. Deterministic State Commitment: Merkle root of lexicographically sorted key-value pairs.
    """

    def __init__(
        self,
        initial_data: dict[str, str] | None = None,
        public_keys: Mapping[str, bytes] | None = None,
        chain_id: str = "lab01",
        crypto: Any = None,
    ) -> None:
        self._data: dict[str, str] = dict(initial_data) if initial_data else {}
        self._nonces: dict[str, int] = {}
        self._executed_tx_hashes: set[bytes] = set()
        self._public_keys: dict[str, bytes] = (
            {k: bytes(v) for k, v in public_keys.items()} if public_keys else {}
        )
        self.chain_id: str = chain_id
        self._crypto: Any = crypto

    def clone(self) -> StateEngine:
        """Produce an isolated deep clone of the state engine for dry-runs."""
        cloned = StateEngine(
            initial_data=dict(self._data),
            public_keys=dict(self._public_keys),
            chain_id=self.chain_id,
            crypto=self._crypto,
        )
        cloned._nonces = dict(self._nonces)
        cloned._executed_tx_hashes = set(self._executed_tx_hashes)
        return cloned

    def register_participant(self, sender: str, public_key: bytes) -> None:
        """Register a participant's public key for transaction verification."""
        if len(public_key) != 32:
            raise ValueError("Ed25519 public key must be 32 bytes")
        self._public_keys[sender] = bytes(public_key)

    def set_public_keys(self, keys: Mapping[str, bytes]) -> None:
        """Update registered participant public keys."""
        for sender, pub_key in keys.items():
            self.register_participant(sender, pub_key)

    def get(self, key: str) -> str | None:
        """Read a value from committed state."""
        return self._data.get(key)

    def get_nonce(self, sender: str) -> int:
        """Get the current committed nonce for a sender (defaults to 0)."""
        return self._nonces.get(sender, 0)

    def is_tx_executed(self, tx_hash: bytes) -> bool:
        """Check if a transaction has already been executed."""
        return tx_hash in self._executed_tx_hashes

    def get_state_hash(self) -> bytes:
        """Compute the current deterministic State Commitment."""
        return compute_state_commitment(self._data)

    def dump_data(self) -> dict[str, str]:
        """Return a copy of the state data dictionary."""
        return dict(self._data)

    def verify_transaction_signature(self, tx: Transaction) -> bool:
        """Verify transaction signature using Ed25519 and domain separation TX:<chain_id>."""
        if not tx.sig:
            return False

        pub_key = self._public_keys.get(tx.sender)
        if pub_key is None:
            # Unknown sender or missing public key -> fail closed
            return False

        payload = tx_signing_payload(tx)
        domain = f"{DOMAIN_TX}:{self.chain_id}"
        if self._crypto is not None and hasattr(self._crypto, "verify"):
            return self._crypto.verify(pub_key, domain, payload, tx.sig)
        return verify(pub_key, domain, payload, tx.sig)

    def apply_transaction(self, tx: Transaction) -> bool:
        """Validate and apply a single transaction to the state.

        Rules:
        - Rule 0: Transaction must have valid Ed25519 signature under TX:<chain_id>
        - Rule 1: Sender 'Alice' can only write to 'Alice/...'
        - Rule 2: tx_hash must not be previously executed
        - Rule 3: tx.nonce must be incremental (supports 0-based or 1-based initial nonce)
        """
        # Rule 0: Cryptographic Signature Verification
        if not self.verify_transaction_signature(tx):
            return False

        # Rule 1: Ownership Access Control
        expected_prefix = f"{tx.sender}/"
        if not tx.key.startswith(expected_prefix):
            return False

        # Rule 2: Replay Protection by Hash
        tx_hash = compute_tx_hash(tx)
        if tx_hash in self._executed_tx_hashes:
            return False

        # Rule 3: Replay Protection by Monotonic Nonce
        if tx.sender not in self._nonces:
            if tx.nonce not in (0, 1):
                return False
        else:
            if tx.nonce != self._nonces[tx.sender] + 1:
                return False

        # State Transition
        self._data[tx.key] = tx.value
        self._nonces[tx.sender] = tx.nonce
        self._executed_tx_hashes.add(tx_hash)
        return True

    def validate_and_compute_hash(
        self,
        tx_list: Sequence[Transaction],
    ) -> tuple[bool, bytes]:
        """Dry-run a list of transactions on a cloned state (StateProtocol contract).

        Returns:
            (True, new_state_hash) if all transactions are valid.
            (False, b"") if any transaction fails validation.
        """
        dry_run_state = self.clone()
        for tx in tx_list:
            if not dry_run_state.apply_transaction(tx):
                return False, b""

        return True, dry_run_state.get_state_hash()

    def commit_block(self, block: BlockBody) -> bytes:
        """Apply all block transactions atomically to the state and return resulting state hash.

        Satisfies StateProtocol: commit_block(self, block: BlockBody) -> bytes.
        Atomicity guarantee: If any transaction fails, state is completely unchanged.
        """
        if not isinstance(block, BlockBody):
            raise TypeError(f"Expected BlockBody, got {type(block).__name__}")

        staging = self.clone()
        for tx in block.transactions:
            if not staging.apply_transaction(tx):
                raise ValueError(f"Invalid transaction in block: {tx}")

        # Atomically commit state changes only after all transactions succeed
        self._data = staging._data
        self._nonces = staging._nonces
        self._executed_tx_hashes = staging._executed_tx_hashes
        return self.get_state_hash()


# Canonical alias for extension point compatibility
State = StateEngine

__all__ = ["State", "StateEngine"]
