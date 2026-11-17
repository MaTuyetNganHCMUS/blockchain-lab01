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
from ..common.serializer import encode_canonical


def sha256(data: bytes) -> bytes:
    """Compute standard SHA-256 digest."""
    return hashlib.sha256(data).digest()


def tx_signing_payload(tx: Transaction) -> bytes:
    """Compute canonical deterministic signing payload of a Transaction under TX:<chain_id>."""
    return encode_canonical(
        {
            "sender": tx.sender,
            "key": tx.key,
            "value": tx.value,
            "nonce": tx.nonce,
        }
    )


def compute_tx_hash(tx: Transaction) -> bytes:
    """Compute deterministic SHA-256 hash of a Transaction using canonical encoding."""
    return sha256(tx_signing_payload(tx))


def header_signing_payload(header: BlockHeader) -> bytes:
    """Compute canonical deterministic signing payload of a BlockHeader under HEADER:<chain_id>."""
    from ..common.interfaces import BlockHeader

    raw = BlockHeader(
        height=header.height,
        round=header.round,
        parent_hash=header.parent_hash,
        state_hash=header.state_hash,
        tx_root=header.tx_root,
        proposer=header.proposer,
        sig=b"",
    )
    return encode_canonical(raw)


def compute_header_hash(header: BlockHeader) -> bytes:
    """Compute deterministic SHA-256 hash of a BlockHeader using canonical encoding."""
    raw = encode_canonical((
        header.height,
        header.round,
        header.parent_hash,
        header.state_hash,
        header.tx_root,
        header.proposer,
    ))
    return sha256(raw)


def compute_merkle_root(leaf_hashes: Sequence[bytes]) -> bytes:
    """Compute binary Merkle tree root from an ordered list of leaf hashes.

    Uses RFC 6962 tree construction and domain separation (0x01 prefix for
    internal nodes) to prevent second-preimage attacks and duplicate-leaf
    vulnerabilities (eliminating CVE-2012-2459 without duplicating odd leaves).
    """
    if not leaf_hashes:
        return sha256(b"")

    if len(leaf_hashes) == 1:
        return leaf_hashes[0]

    # Split at the largest power of 2 strictly less than len(leaf_hashes)
    k = 1 << ((len(leaf_hashes) - 1).bit_length() - 1)
    left = compute_merkle_root(leaf_hashes[:k])
    right = compute_merkle_root(leaf_hashes[k:])
    return sha256(b"\x01" + left + right)


def compute_state_commitment(data: dict[str, str]) -> bytes:
    """Compute deterministic State Commitment Merkle Root.

    1. Sorts all key-value pairs lexicographically by key.
    2. Hashes each leaf as sha256(0x00 + canonical_encode((key, value))).
    3. Builds binary Merkle tree with domain separation to produce 32-byte state root.
    """
    if not data:
        return sha256(b"EMPTY_STATE")

    sorted_items = sorted(data.items(), key=lambda kv: kv[0])
    leaf_hashes = [
        sha256(b"\x00" + encode_canonical((k, v))) for k, v in sorted_items
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
    "header_signing_payload",
    "sha256",
    "tx_signing_payload",
]
