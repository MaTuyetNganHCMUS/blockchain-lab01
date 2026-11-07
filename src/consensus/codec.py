"""Serialization and deserialization codecs for network consensus packets.

Converts core data models (Transaction, BlockHeader, BlockBody, Vote) into
canonical JSON-wire byte payloads and reconstructs them safely.
"""

from __future__ import annotations

import json
from typing import Any

from ..common.interfaces import (
    BlockBody,
    BlockHeader,
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    MSG_TX,
    MSG_VOTE,
    Transaction,
    Vote,
)


def tx_to_dict(tx: Transaction) -> dict[str, Any]:
    """Convert a Transaction to a JSON-serializable dictionary."""
    return {
        "sender": tx.sender,
        "key": tx.key,
        "value": tx.value,
        "nonce": tx.nonce,
        "sig": tx.sig.hex(),
    }


def dict_to_tx(d: dict[str, Any]) -> Transaction:
    """Reconstruct a Transaction from a dictionary."""
    return Transaction(
        sender=str(d["sender"]),
        key=str(d["key"]),
        value=str(d["value"]),
        nonce=int(d["nonce"]),
        sig=bytes.fromhex(d.get("sig", "")),
    )


def header_to_dict(h: BlockHeader) -> dict[str, Any]:
    """Convert a BlockHeader to a JSON-serializable dictionary."""
    return {
        "height": h.height,
        "round": h.round,
        "parent_hash": h.parent_hash.hex(),
        "state_hash": h.state_hash.hex(),
        "tx_root": h.tx_root.hex(),
        "proposer": h.proposer,
        "sig": h.sig.hex(),
    }


def dict_to_header(d: dict[str, Any]) -> BlockHeader:
    """Reconstruct a BlockHeader from a dictionary."""
    return BlockHeader(
        height=int(d["height"]),
        round=int(d["round"]),
        parent_hash=bytes.fromhex(d["parent_hash"]),
        state_hash=bytes.fromhex(d["state_hash"]),
        tx_root=bytes.fromhex(d["tx_root"]),
        proposer=str(d["proposer"]),
        sig=bytes.fromhex(d.get("sig", "")),
    )


def body_to_dict(b: BlockBody) -> dict[str, Any]:
    """Convert a BlockBody to a JSON-serializable dictionary."""
    return {
        "header_hash": b.header_hash.hex(),
        "transactions": [tx_to_dict(tx) for tx in b.transactions],
    }


def dict_to_body(d: dict[str, Any]) -> BlockBody:
    """Reconstruct a BlockBody from a dictionary."""
    tx_list = [dict_to_tx(t) for t in d.get("transactions", [])]
    return BlockBody(
        header_hash=bytes.fromhex(d["header_hash"]),
        transactions=tuple(tx_list),
    )


def vote_to_dict(v: Vote) -> dict[str, Any]:
    """Convert a Vote to a JSON-serializable dictionary."""
    return {
        "height": v.height,
        "round": v.round,
        "phase": v.phase,
        "block_hash": v.block_hash.hex(),
        "voter": v.voter,
        "sig": v.sig.hex(),
    }


def dict_to_vote(d: dict[str, Any]) -> Vote:
    """Reconstruct a Vote from a dictionary."""
    return Vote(
        height=int(d["height"]),
        round=int(d["round"]),
        phase=str(d["phase"]),
        block_hash=bytes.fromhex(d["block_hash"]),
        voter=str(d["voter"]),
        sig=bytes.fromhex(d.get("sig", "")),
    )


def pack_message(msg_type: str, data: Any) -> bytes:
    """Pack a typed object into wire bytes."""
    envelope: dict[str, Any] = {"msg_type": msg_type}

    if msg_type == MSG_HEADER and isinstance(data, BlockHeader):
        envelope["data"] = header_to_dict(data)
    elif msg_type == MSG_BODY and isinstance(data, BlockBody):
        envelope["data"] = body_to_dict(data)
    elif msg_type == MSG_VOTE and isinstance(data, Vote):
        envelope["data"] = vote_to_dict(data)
    elif msg_type == MSG_TX and isinstance(data, Transaction):
        envelope["data"] = tx_to_dict(data)
    elif msg_type == MSG_BODY_REQUEST and isinstance(data, (bytes, bytearray)):
        envelope["data"] = bytes(data).hex()
    elif isinstance(data, dict):
        envelope["data"] = data
    else:
        envelope["data"] = str(data)

    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def unpack_message(payload_bytes: bytes) -> tuple[str, Any]:
    """Unpack wire bytes back into (msg_type, typed_object)."""
    envelope = json.loads(payload_bytes.decode("utf-8"))
    msg_type = envelope.get("msg_type", "UNKNOWN")
    raw_data = envelope.get("data")

    if not isinstance(raw_data, dict):
        if msg_type == MSG_BODY_REQUEST and isinstance(raw_data, str):
            return msg_type, bytes.fromhex(raw_data)
        return msg_type, raw_data

    if msg_type == MSG_HEADER:
        return msg_type, dict_to_header(raw_data)
    if msg_type == MSG_BODY:
        return msg_type, dict_to_body(raw_data)
    if msg_type == MSG_VOTE:
        return msg_type, dict_to_vote(raw_data)
    if msg_type == MSG_TX:
        return msg_type, dict_to_tx(raw_data)

    return msg_type, raw_data
