"""Cryptography extension point for TV1."""

import hashlib


def hash_bytes(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def encode_canonical(obj: object) -> bytes:
    """Temporary deterministic representation; replace with the agreed encoder."""
    return repr(obj).encode("utf-8")


def sign(priv_key: bytes, domain: str, payload_bytes: bytes) -> bytes:
    raise NotImplementedError("Ed25519 signing is assigned to TV1.")


def verify(pub_key: bytes, domain: str, payload_bytes: bytes, sig: bytes) -> bool:
    raise NotImplementedError("Ed25519 verification is assigned to TV1.")
