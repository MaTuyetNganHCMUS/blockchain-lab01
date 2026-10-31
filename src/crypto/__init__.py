"""Cryptographic primitives."""

from .ed25519 import encode_canonical, hash_bytes, sign, verify

__all__ = ["encode_canonical", "hash_bytes", "sign", "verify"]
