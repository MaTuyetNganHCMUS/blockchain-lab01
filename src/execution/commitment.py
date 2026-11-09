"""State Commitment & Merkle Tree calculation module.
Responsible for computing deterministic state roots and transaction roots.
Owner: TV2 (Execution Engine & State Commitment)
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..common.interfaces import BlockHeader, Transaction


def sha256(data: bytes) -> bytes:
    """Compute standard SHA-256 digest."""
    return hashlib.sha256(data).digest()


def compute_tx_hash(tx: Transaction) -> bytes:
    """Compute deterministic SHA-256 hash of a Transaction."""
    raw = f"{tx.sender}:{tx.key}:{tx.value}:{tx.nonce}".encode()
    return sha256(raw)


def compute_header_hash(header: BlockHeader) -> bytes:
    """Compute deterministic SHA-256 hash of a BlockHeader."""
    raw = (
        f"{header.height}:{header.round}:"
        f"{header.parent_hash.hex()}:{header.state_hash.hex()}:"
        f"{header.tx_root.hex()}:{header.proposer}"
    ).encode()
    return sha256(raw)


def compute_merkle_root(leaf_hashes: Sequence[bytes]) -> bytes:
    """Compute binary Merkle tree root from an ordered list of leaf hashes.

    If leaf_hashes is empty, returns sha256(b"").
    If odd number of leaves at any level, duplicates the last node.
    """
    if not leaf_hashes:
        return sha256(b"")

    current_level = list(leaf_hashes)

    while len(current_level) > 1:
        next_level: list[bytes] = []
        if len(current_level) % 2 != 0:
            current_level.append(current_level[-1])

        for i in range(0, len(current_level), 2):
            left = current_level[i]
            right = current_level[i + 1]
            combined = sha256(left + right)
            next_level.append(combined)

        current_level = next_level

    return current_level[0]


def compute_state_commitment(data: dict[str, str]) -> bytes:
    """Compute deterministic State Commitment Merkle Root.

    1. Sorts all key-value pairs lexicographically by key.
    2. Hashes each leaf as sha256(f"{key}:{value}".encode("utf-8")).
    3. Builds binary Merkle tree to produce 32-byte state root.
    """
    if not data:
        return sha256(b"EMPTY_STATE")

    sorted_items = sorted(data.items(), key=lambda kv: kv[0])
    leaf_hashes = [
        sha256(f"{k}:{v}".encode()) for k, v in sorted_items
    ]
    return compute_merkle_root(leaf_hashes)


def compute_tx_root(tx_hashes: Sequence[bytes]) -> bytes:
    """Compute deterministic Merkle Root for a block's transaction list."""
    if not tx_hashes:
        return sha256(b"EMPTY_TX_ROOT")
    return compute_merkle_root(tx_hashes)


__all__ = [
    "compute_header_hash",
    "compute_merkle_root",
    "compute_state_commitment",
    "compute_tx_hash",
    "compute_tx_root",
    "sha256",
]
