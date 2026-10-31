"""Domain model aliases kept stable for the five implementation branches."""

from .interfaces import (
    GENESIS_PARENT_HASH,
    NIL_BLOCK_HASH,
    BlockBody,
    BlockHeader,
    NetPacket,
    QuorumResult,
    Transaction,
    Vote,
)

__all__ = [
    "BlockBody",
    "BlockHeader",
    "GENESIS_PARENT_HASH",
    "NIL_BLOCK_HASH",
    "NetPacket",
    "QuorumResult",
    "Transaction",
    "Vote",
]
