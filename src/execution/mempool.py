"""Bounded, deduplicated transaction mempool and admission policy (TV2/TV4)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ..common.interfaces import (
    MSG_BODY,
    MSG_TX,
    REASON_MEMPOOL_FULL,
    REASON_TX_BAD_SIGNATURE,
    REASON_TX_DUPLICATE,
    REASON_TX_NONCE_INVALID,
    REASON_TX_TOO_LARGE,
    REASON_TX_UNAUTHORIZED_KEY,
    BlockBody,
    BlockPolicy,
    Transaction,
)
from .commitment import compute_tx_hash


def tx_wire_size(tx: Transaction) -> int:
    """Return canonical wire size in bytes for a single Transaction packet."""
    envelope = {
        "msg_type": MSG_TX,
        "data": {
            "sender": tx.sender,
            "key": tx.key,
            "value": tx.value,
            "nonce": tx.nonce,
            "sig": tx.sig.hex(),
        },
    }
    return len(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))


def body_wire_size(body: BlockBody) -> int:
    """Return canonical wire size in bytes for a BlockBody packet."""
    envelope = {
        "msg_type": MSG_BODY,
        "data": {
            "header_hash": body.header_hash.hex(),
            "transactions": [
                {
                    "sender": tx.sender,
                    "key": tx.key,
                    "value": tx.value,
                    "nonce": tx.nonce,
                    "sig": tx.sig.hex(),
                }
                for tx in body.transactions
            ],
        },
    }
    return len(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class MempoolConfig:
    """Resource bounds and deterministic nonce/TTL policy for Mempool."""

    max_txs: int = 1000
    max_bytes: int = 1_048_576
    max_tx_bytes: int = 16_384
    tx_ttl: float | None = None
    future_nonce_window: int = 64

    def __post_init__(self) -> None:
        if self.max_txs <= 0:
            raise ValueError("MempoolConfig.max_txs must be positive")
        if self.max_bytes <= 0:
            raise ValueError("MempoolConfig.max_bytes must be positive")
        if self.max_tx_bytes <= 0:
            raise ValueError("MempoolConfig.max_tx_bytes must be positive")
        if self.tx_ttl is not None and self.tx_ttl <= 0:
            raise ValueError("MempoolConfig.tx_ttl must be positive when set")
        if self.future_nonce_window < 0:
            raise ValueError("MempoolConfig.future_nonce_window must be non-negative")


@dataclass(frozen=True)
class AdmissionResult:
    """Unified return contract for local and network transaction admission."""

    accepted: bool
    reason_code: str
    tx_hash: bytes
    size_bytes: int


@dataclass(frozen=True)
class _MempoolEntry:
    tx: Transaction
    size_bytes: int
    admitted_at: float


class Mempool:
    """Deterministic bounded mempool indexed by tx_hash and (sender, nonce)."""

    def __init__(self, config: MempoolConfig | None = None) -> None:
        self.config = config if config is not None else MempoolConfig()
        self._entries: dict[bytes, _MempoolEntry] = {}
        self._sender_nonces: dict[tuple[str, int], bytes] = {}
        self._total_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[Transaction]:
        for entry in self._entries.values():
            yield entry.tx

    def __getitem__(self, index: int) -> Transaction:
        return list(self._entries.values())[index].tx

    def __contains__(self, item: object) -> bool:
        if isinstance(item, bytes):
            return item in self._entries
        if isinstance(item, Transaction):
            return compute_tx_hash(item) in self._entries
        return False

    def append(self, tx: Transaction) -> bool:
        """List-compatible admission shortcut used by unit tests."""
        return self.admit(tx).accepted

    def clear(self) -> None:
        self._entries.clear()
        self._sender_nonces.clear()
        self._total_bytes = 0

    def evict_expired(self, sim_time: float) -> int:
        """Evict pending transactions whose age exceeds ``config.tx_ttl``."""
        if self.config.tx_ttl is None:
            return 0
        expired = [
            tx_hash
            for tx_hash, entry in self._entries.items()
            if (float(sim_time) - entry.admitted_at) > self.config.tx_ttl
        ]
        return self.remove_by_hashes(expired)

    def remove_by_hashes(self, tx_hashes: Iterable[bytes]) -> int:
        """Remove transactions by hash in O(1) per hash."""
        removed = 0
        for tx_hash in tx_hashes:
            entry = self._entries.pop(tx_hash, None)
            if entry is None:
                continue
            self._total_bytes -= entry.size_bytes
            nonce_key = (entry.tx.sender, entry.tx.nonce)
            if self._sender_nonces.get(nonce_key) == tx_hash:
                del self._sender_nonces[nonce_key]
            removed += 1
        return removed

    def remove_finalized(
        self,
        transactions: Sequence[Transaction],
        *,
        state: Any = None,
    ) -> int:
        """Remove finalized transactions by hash and prune stale committed nonces."""
        removed = self.remove_by_hashes(compute_tx_hash(tx) for tx in transactions)
        if state is None:
            return removed

        stale_hashes: list[bytes] = []
        committed_nonces = getattr(state, "_nonces", None)
        if not isinstance(committed_nonces, dict):
            committed_nonces = getattr(state, "nonces", None)
        if isinstance(committed_nonces, dict):
            for tx_hash, entry in self._entries.items():
                sender = entry.tx.sender
                if sender in committed_nonces and entry.tx.nonce <= committed_nonces[sender]:
                    stale_hashes.append(tx_hash)
        return removed + self.remove_by_hashes(stale_hashes)

    def admit(
        self,
        tx: Transaction,
        *,
        state: Any = None,
        sim_time: float = 0.0,
    ) -> AdmissionResult:
        """Validate and insert ``tx`` using deterministic cheap-to-expensive checks."""
        if not isinstance(tx, Transaction) or not tx.sender or not tx.key or tx.nonce < 0:
            return AdmissionResult(False, REASON_TX_NONCE_INVALID, b"", 0)

        if self.config.tx_ttl is not None:
            self.evict_expired(sim_time)

        size_bytes = tx_wire_size(tx)
        if size_bytes > self.config.max_tx_bytes:
            return AdmissionResult(False, REASON_TX_TOO_LARGE, b"", size_bytes)

        if not tx.key.startswith(f"{tx.sender}/"):
            return AdmissionResult(False, REASON_TX_UNAUTHORIZED_KEY, b"", size_bytes)

        tx_hash = compute_tx_hash(tx)
        if tx_hash in self._entries:
            return AdmissionResult(False, REASON_TX_DUPLICATE, tx_hash, size_bytes)

        if state is not None and hasattr(state, "is_tx_executed") and state.is_tx_executed(tx_hash):
            return AdmissionResult(False, REASON_TX_DUPLICATE, tx_hash, size_bytes)

        if (tx.sender, tx.nonce) in self._sender_nonces:
            return AdmissionResult(False, REASON_TX_DUPLICATE, tx_hash, size_bytes)

        if (
            state is not None
            and hasattr(state, "verify_transaction_signature")
            and not state.verify_transaction_signature(tx)
        ):
            return AdmissionResult(False, REASON_TX_BAD_SIGNATURE, tx_hash, size_bytes)

        if state is not None and not self._check_nonce_policy(tx, state):
            return AdmissionResult(False, REASON_TX_NONCE_INVALID, tx_hash, size_bytes)

        if len(self._entries) >= self.config.max_txs or (
            self._total_bytes + size_bytes > self.config.max_bytes
        ):
            return AdmissionResult(False, REASON_MEMPOOL_FULL, tx_hash, size_bytes)

        self._entries[tx_hash] = _MempoolEntry(
            tx=tx,
            size_bytes=size_bytes,
            admitted_at=float(sim_time),
        )
        self._sender_nonces[(tx.sender, tx.nonce)] = tx_hash
        self._total_bytes += size_bytes
        return AdmissionResult(True, "", tx_hash, size_bytes)

    def _check_nonce_policy(self, tx: Transaction, state: Any) -> bool:
        committed_nonces = getattr(state, "_nonces", None)
        if isinstance(committed_nonces, dict):
            if tx.sender in committed_nonces:
                committed = committed_nonces[tx.sender]
                if tx.nonce <= committed:
                    return False
                return tx.nonce <= committed + 1 + self.config.future_nonce_window
            return tx.nonce <= 1 + self.config.future_nonce_window

        mock_nonces = getattr(state, "nonces", None)
        if isinstance(mock_nonces, dict):
            if tx.sender in mock_nonces:
                committed = mock_nonces[tx.sender]
                if tx.nonce <= committed:
                    return False
                return tx.nonce <= committed + 1 + self.config.future_nonce_window
            return tx.nonce <= 1 + self.config.future_nonce_window
        return True

    def select_candidates(
        self,
        state: Any,
        block_policy: BlockPolicy | None = None,
    ) -> tuple[tuple[Transaction, ...], bytes]:
        """Select deterministic subset of valid transactions bounded by ``block_policy``."""
        policy = block_policy if block_policy is not None else BlockPolicy()
        # Sort each sender's pending transactions by ascending nonce while keeping
        # first-seen sender order deterministic.
        sender_order: dict[str, int] = {}
        ordered_entries: list[_MempoolEntry] = []
        for entry in self._entries.values():
            sender_order.setdefault(entry.tx.sender, len(sender_order))
            ordered_entries.append(entry)
        ordered_entries.sort(key=lambda e: (sender_order[e.tx.sender], e.tx.nonce))

        accepted: list[Transaction] = []
        for entry in ordered_entries:
            if len(accepted) >= policy.max_txs:
                break
            trial = tuple(accepted + [entry.tx])
            if body_wire_size(BlockBody(header_hash=b"\x00" * 32, transactions=trial)) > policy.max_bytes:
                continue
            tx_ok, _ = state.validate_and_compute_hash(trial)
            if tx_ok:
                accepted.append(entry.tx)

        final_txs = tuple(accepted)
        _, final_hash = state.validate_and_compute_hash(final_txs)
        return final_txs, final_hash


__all__ = [
    "AdmissionResult",
    "Mempool",
    "MempoolConfig",
    "body_wire_size",
    "tx_wire_size",
]
