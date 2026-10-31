"""Shared contracts agreed by all implementation branches."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Transaction:
    sender: str
    key: str
    value: str
    nonce: int
    sig: bytes = b""


@dataclass(frozen=True)
class BlockHeader:
    height: int
    round: int
    parent_hash: bytes
    state_hash: bytes
    tx_root: bytes
    proposer: str
    sig: bytes = b""


@dataclass(frozen=True)
class BlockBody:
    header_hash: bytes
    transactions: tuple[Transaction, ...] = ()


@dataclass(frozen=True)
class Vote:
    height: int
    round: int
    phase: str
    block_hash: bytes
    voter: str
    sig: bytes = b""


@dataclass(frozen=True)
class NetPacket:
    msg_type: str
    sender: str
    receiver: str
    payload: bytes
