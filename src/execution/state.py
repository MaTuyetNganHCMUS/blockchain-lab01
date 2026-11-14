"""Key-Value State Engine with Ownership Validation & Replay Protection.
Owner: TV2 (Execution Engine & State Commitment)
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..common.interfaces import BlockBody, BlockHeader, CryptoProtocol, Transaction
from ..common.serializer import encode_canonical
from .commitment import compute_state_commitment, compute_tx_hash


class StateEngine:
    """Deterministic Key-Value State Engine satisfying StateProtocol.

    Enforces:
    1. Signature Verification: Valid signature under TX:<chain_id> domain when sender key is registered.
    2. Prefix-based Access Control: Sender 'X' can only write to key 'X/...'.
    3. Replay Protection: Sequential nonces and executed tx hash tracking.
    4. Dry-run Isolation: Ability to validate tx lists without mutating base state.
    5. Deterministic State Commitment: Merkle root of lexicographically sorted key-value pairs.
    """

    def __init__(
        self,
        initial_data: dict[str, str] | None = None,
        crypto: CryptoProtocol | None = None,
        pub_keys: Mapping[str, bytes] | None = None,
        chain_id: str = "lab01",
    ) -> None:
        self._data: dict[str, str] = dict(initial_data) if initial_data else {}
        self._nonces: dict[str, int] = {}
        self._executed_tx_hashes: set[bytes] = set()
        self.crypto = crypto
        self.pub_keys = dict(pub_keys) if pub_keys else {}
        self.chain_id = chain_id

    def clone(self) -> StateEngine:
        """Produce an isolated deep clone of the state engine for dry-runs."""
        cloned = StateEngine(
            initial_data=self._data,
            crypto=self.crypto,
            pub_keys=self.pub_keys,
            chain_id=self.chain_id,
        )
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
        - Cryptographic signature must verify under TX:<chain_id> if sender key is registered
        - Sender 'Alice' can only write to 'Alice/...'
        - tx.nonce must be incremental (supports 0-based or 1-based initial nonce)
        - tx_hash must not be previously executed
        """
        # Rule 0: Cryptographic Signature Verification (Section 7 Lab01)
        if self.crypto is not None and tx.sender in self.pub_keys:
            if not tx.sig:
                return False
            payload = encode_canonical((tx.sender, tx.key, tx.value, tx.nonce))
            if not self.crypto.verify(
                self.pub_keys[tx.sender],
                f"TX:{self.chain_id}",
                payload,
                tx.sig,
            ):
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
