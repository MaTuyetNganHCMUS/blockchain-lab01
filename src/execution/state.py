"""Key-Value State Engine with Ownership Validation & Replay Protection.
Owner: TV2 (Execution Engine & State Commitment)
"""
from __future__ import annotations

from collections.abc import Sequence

from ..common.interfaces import BlockBody, BlockHeader, Transaction
from .commitment import compute_state_commitment, compute_tx_hash


class StateEngine:
    """Deterministic Key-Value State Engine satisfying StateProtocol.

    Enforces:
    1. Prefix-based Access Control: Sender 'X' can only write to key 'X/...'.
    2. Replay Protection: Sequential nonces and executed tx hash tracking.
    3. Dry-run Isolation: Ability to validate tx lists without mutating base state.
    4. Deterministic State Commitment: Merkle root of lexicographically sorted key-value pairs.
    """

    def __init__(self, initial_data: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = dict(initial_data) if initial_data else {}
        self._nonces: dict[str, int] = {}
        self._executed_tx_hashes: set[bytes] = set()

    def clone(self) -> StateEngine:
        """Produce an isolated deep clone of the state engine for dry-runs."""
        cloned = StateEngine()
        cloned._data = dict(self._data)
        cloned._nonces = dict(self._nonces)
        cloned._executed_tx_hashes = set(self._executed_tx_hashes)
        return cloned

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

    def apply_transaction(self, tx: Transaction) -> bool:
        """Validate and apply a single transaction to the state.

        Rules:
        - Sender 'Alice' can only write to 'Alice/...'
        - tx.nonce must be incremental (supports 0-based or 1-based initial nonce)
        - tx_hash must not be previously executed
        """
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
        """Apply all block transactions to the state and return resulting state hash.

        Satisfies StateProtocol: commit_block(self, block: BlockBody) -> bytes.
        """
        if not isinstance(block, BlockBody):
            raise TypeError(f"Expected BlockBody, got {type(block).__name__}")

        for tx in block.transactions:
            if not self.apply_transaction(tx):
                raise ValueError(f"Invalid transaction in block: {tx}")

        return self.get_state_hash()


# Canonical alias for extension point compatibility
State = StateEngine

__all__ = ["State", "StateEngine"]
