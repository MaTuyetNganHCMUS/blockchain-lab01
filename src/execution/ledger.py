"""Ledger and Local View Store.
Manages pending proposed blocks and the finalized immutable blockchain.
Owner: TV2 (Execution Engine & State Commitment)
"""
from __future__ import annotations

from ..common.interfaces import (
    GENESIS_PARENT_HASH,
    BlockBody,
    BlockHeader,
    StateProtocol,
)
from .commitment import (
    compute_header_hash,
    compute_state_commitment,
    compute_tx_hash,
    compute_tx_root,
)


def create_genesis_block() -> tuple[BlockHeader, BlockBody]:
    """Create the deterministic Genesis block (Height 0)."""
    empty_state_hash = compute_state_commitment({})
    empty_tx_root = compute_tx_root([])

    header = BlockHeader(
        height=0,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=empty_state_hash,
        tx_root=empty_tx_root,
        proposer="GENESIS",
        sig=b"",
    )
    body = BlockBody(header_hash=compute_header_hash(header), transactions=[])
    return header, body


class Ledger:
    """Manages the two-view ledger satisfying LedgerProtocol:
    1. Pending View: Proposed blocks waiting for consensus quorum.
    2. Finalized Chain: Sequentially committed, immutable blocks.
    """

    def __init__(self, with_genesis: bool = True) -> None:
        self._finalized_chain: list[tuple[BlockHeader, BlockBody]] = []
        self._pending_blocks: dict[bytes, tuple[BlockHeader, BlockBody]] = {}
        self._blocks_by_height: dict[int, tuple[BlockHeader, BlockBody]] = {}
        self._blocks_by_hash: dict[bytes, tuple[BlockHeader, BlockBody]] = {}

        if with_genesis:
            genesis_header, genesis_body = create_genesis_block()
            self.add_finalized_block(genesis_header, genesis_body)

    @property
    def height(self) -> int:
        """Current height of the finalized blockchain."""
        if not self._finalized_chain:
            return -1
        return self._finalized_chain[-1][0].height

    def get_latest_header(self) -> BlockHeader | None:
        """Return header of the latest finalized block."""
        if not self._finalized_chain:
            return None
        return self._finalized_chain[-1][0]

    def get_latest_hash(self) -> bytes:
        """Return the hash of the latest finalized block."""
        header = self.get_latest_header()
        if header is None:
            return GENESIS_PARENT_HASH
        return compute_header_hash(header)

    def get_block(self, height: int) -> tuple[BlockHeader, BlockBody] | None:
        """Retrieve block by height."""
        return self._blocks_by_height.get(height)

    def get_by_height(self, height: int) -> tuple[BlockHeader, BlockBody] | None:
        """Retrieve block at specified height (LedgerProtocol convenience)."""
        return self._blocks_by_height.get(height)

    def is_finalized(self, height: int, header_hash: bytes) -> bool:
        """Check whether a block hash is finalized at the given height."""
        entry = self._blocks_by_height.get(height)
        if entry is None:
            return False
        return entry[1].header_hash == header_hash or compute_header_hash(entry[0]) == header_hash

    def add_pending_block(self, header: BlockHeader, body: BlockBody) -> bytes:
        """Store a proposed block in the pending pool."""
        header_hash = body.header_hash if body.header_hash else compute_header_hash(header)
        self._pending_blocks[header_hash] = (header, body)
        return header_hash

    def get_pending_block(self, block_hash: bytes) -> tuple[BlockHeader, BlockBody] | None:
        """Retrieve a pending block by header hash."""
        return self._pending_blocks.get(block_hash)

    def validate_block(
        self,
        header: BlockHeader,
        body: BlockBody,
        current_state: StateProtocol,
    ) -> tuple[bool, str]:
        """Validate block integrity before committing or voting.

        Checks:
        1. Body header_hash must match compute_header_hash(header).
        2. Parent hash must match the latest finalized block's hash.
        3. Height must be strictly latest_height + 1.
        4. Merkle tx_root must match hash of transactions in body.
        5. Dry-run execution of transactions on state must succeed.
        6. Resulting state_hash must match header.state_hash.
        """
        # 1. Header Hash Link
        expected_header_hash = compute_header_hash(header)
        if body.header_hash and body.header_hash != expected_header_hash:
            return False, "Body header_hash does not match computed header hash"

        # 2. Parent Hash
        expected_parent = self.get_latest_hash()
        if header.parent_hash != expected_parent:
            return False, (
                f"Parent hash mismatch: expected {expected_parent.hex()}, "
                f"got {header.parent_hash.hex()}"
            )

        # 3. Height Continuity
        expected_height = self.height + 1
        if header.height != expected_height:
            return False, (
                f"Height discontinuity: expected {expected_height}, "
                f"got {header.height}"
            )

        # 4. Transaction Merkle Root
        computed_tx_root = compute_tx_root(
            [compute_tx_hash(tx) for tx in body.transactions]
        )
        if header.tx_root != computed_tx_root:
            return False, (
                f"Tx root mismatch: expected {computed_tx_root.hex()}, "
                f"got {header.tx_root.hex()}"
            )

        # 5 & 6. State Dry-Run Validation
        valid_txs, computed_state_hash = current_state.validate_and_compute_hash(
            body.transactions
        )
        if not valid_txs:
            return False, "One or more transactions in block failed dry-run execution"

        if computed_state_hash != header.state_hash:
            return False, (
                f"State hash mismatch: expected {header.state_hash.hex()}, "
                f"got {computed_state_hash.hex()}"
            )

        return True, "Valid"

    def add_finalized_block(self, header: BlockHeader, body: BlockBody) -> None:
        """Append block to finalized chain and prune from pending pool (LedgerProtocol)."""
        header_hash = body.header_hash if body.header_hash else compute_header_hash(header)
        self._finalized_chain.append((header, body))
        self._blocks_by_height[header.height] = (header, body)
        self._blocks_by_hash[header_hash] = (header, body)
        self._pending_blocks.pop(header_hash, None)

    def get_chain_history(self) -> list[tuple[BlockHeader, BlockBody]]:
        """Return a copy of the entire finalized chain."""
        return list(self._finalized_chain)


__all__ = ["Ledger", "create_genesis_block"]
