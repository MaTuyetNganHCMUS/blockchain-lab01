"""Canonical, deterministic encoding shared by all modules."""

from __future__ import annotations

import struct
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def _length(size: int) -> bytes:
    if size < 0 or size > 0xFFFFFFFF:
        raise ValueError("encoded value is too large")
    return struct.pack(">I", size)


def encode_canonical(value: Any) -> bytes:
    """Encode supported values with explicit tags and deterministic ordering."""
    if value is None:
        return b"\x00"
    if isinstance(value, bool):
        return b"\x01" + (b"\x01" if value else b"\x00")
    if isinstance(value, int):
        try:
            return b"\x02" + value.to_bytes(8, "big", signed=True)
        except OverflowError as exc:
            raise ValueError("integer must fit in signed 64-bit range") from exc
    if isinstance(value, float):
        return b"\x03" + struct.pack(">d", value)
    if isinstance(value, bytes):
        return b"\x04" + _length(len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return b"\x05" + _length(len(raw)) + raw
    if isinstance(value, Enum):
        return b"\x06" + encode_canonical(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return b"\x07" + encode_canonical(
            [(field.name, getattr(value, field.name)) for field in fields(value)]
        )
    if isinstance(value, dict):
        encoded_items = sorted(
            (encode_canonical(key), encode_canonical(item))
            for key, item in value.items()
        )
        return b"\x08" + _length(len(encoded_items)) + b"".join(
            key + item for key, item in encoded_items
        )
    if isinstance(value, (list, tuple)):
        return b"\x09" + _length(len(value)) + b"".join(
            encode_canonical(item) for item in value
        )
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")

__all__ = ["encode_canonical"]
