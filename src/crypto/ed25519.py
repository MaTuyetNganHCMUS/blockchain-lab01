"""Ed25519 signing, domain separation, and SHA-256 hashing."""

from __future__ import annotations

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..common.serializer import encode_canonical


def hash_bytes(payload: bytes) -> bytes:
    """Return a SHA-256 digest of bytes."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return hashlib.sha256(payload).digest()


def _domain_payload(domain: str, payload: bytes) -> bytes:
    if not domain or not isinstance(domain, str):
        raise ValueError("domain must be a non-empty string")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return encode_canonical((domain, payload))


def generate_keypair() -> tuple[bytes, bytes]:
    """Return raw 32-byte Ed25519 private and public keys."""
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes


def sign(private_key: bytes, domain: str, payload: bytes) -> bytes:
    """Sign payload under a context such as ``VOTE:lab01``."""
    if len(private_key) != 32:
        raise ValueError("Ed25519 private keys must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(
        _domain_payload(domain, payload)
    )


def verify(public_key: bytes, domain: str, payload: bytes, signature: bytes) -> bool:
    """Verify a domain-separated signature."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, _domain_payload(domain, payload)
        )
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


class Ed25519Crypto:
    """CryptoProtocol adapter wrapping TV1's Ed25519 and SHA-256 primitives."""

    def encode_canonical(self, obj: object) -> bytes:
        return encode_canonical(obj)

    def hash_bytes(self, payload: bytes) -> bytes:
        return hash_bytes(payload)

    def generate_keypair(self) -> tuple[bytes, bytes]:
        return generate_keypair()

    def sign(self, priv_key: bytes, domain: str, payload_bytes: bytes) -> bytes:
        return sign(priv_key, domain, payload_bytes)

    def verify(self, pub_key: bytes, domain: str, payload_bytes: bytes, sig: bytes) -> bool:
        return verify(pub_key, domain, payload_bytes, sig)


CryptoService = Ed25519Crypto

__all__ = [
    "CryptoService",
    "Ed25519Crypto",
    "encode_canonical",
    "generate_keypair",
    "hash_bytes",
    "sign",
    "verify",
]
