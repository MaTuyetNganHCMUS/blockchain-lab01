"""TV1 unit tests for models, canonical encoding, and cryptography."""

from dataclasses import FrozenInstanceError

import pytest

from src.common.interfaces import Transaction
from src.common.serializer import encode_canonical
from src.crypto import (
    ValidatorIdentity,
    ValidatorKeyRegistry,
    generate_keypair,
    hash_bytes,
    sign,
    verify,
)


def _transaction_payload(tx: Transaction) -> bytes:
    return encode_canonical(
        {
            "sender": tx.sender,
            "key": tx.key,
            "value": tx.value,
            "nonce": tx.nonce,
        }
    )


def test_immutable_transaction() -> None:
    tx = Transaction("Alice", "Alice/name", "Lab01", 0)
    with pytest.raises(FrozenInstanceError):
        tx.nonce = 1  # type: ignore[misc]


def test_dictionary_encoding_is_order_independent() -> None:
    first = encode_canonical({"z": 1, "a": {"b": 2, "a": 3}})
    second = encode_canonical({"a": {"a": 3, "b": 2}, "z": 1})
    assert first == second


def test_sha256_returns_32_bytes() -> None:
    assert hash_bytes(b"payload") == hash_bytes(b"payload")
    assert len(hash_bytes(b"payload")) == 32


def test_domain_separation_rejects_cross_context_and_payload_changes() -> None:
    private_key, public_key = generate_keypair()
    payload = encode_canonical({"height": 1, "block_hash": b"block-a"})
    signature = sign(private_key, "VOTE:lab01", payload)

    assert verify(public_key, "VOTE:lab01", payload, signature)
    assert not verify(public_key, "HEADER:lab01", payload, signature)
    assert not verify(public_key, "VOTE:other-chain", payload, signature)
    assert not verify(public_key, "VOTE:lab01", payload + b"\x00", signature)


def test_wrong_key_and_wrong_lengths_are_rejected() -> None:
    private_key, public_key = generate_keypair()
    _, other_public_key = generate_keypair()
    signature = sign(private_key, "TX:lab01", b"tx")

    assert not verify(other_public_key, "TX:lab01", b"tx", signature)
    assert not verify(public_key, "TX:lab01", b"tx", b"short")
    with pytest.raises(ValueError):
        sign(b"short", "TX:lab01", b"tx")
    with pytest.raises(ValueError):
        sign(private_key, "", b"tx")


def test_validator_identity_and_fixed_registry() -> None:
    _, public_key = generate_keypair()
    identity = ValidatorIdentity("node0", public_key)
    registry = ValidatorKeyRegistry((identity,))

    assert registry.validator_ids == ("node0",)
    assert registry.get_public_key("node0") == public_key
    assert registry.contains("node0")
    with pytest.raises(KeyError):
        registry.get_public_key("unknown")
    with pytest.raises(ValueError):
        ValidatorIdentity("node1", b"invalid")


def test_signed_transaction_payload_is_stable() -> None:
    private_key, public_key = generate_keypair()
    tx = Transaction("Alice", "Alice/name", "Lab01", 1)
    signature = sign(private_key, "TX:lab01", _transaction_payload(tx))
    signed_tx = Transaction(tx.sender, tx.key, tx.value, tx.nonce, signature)

    assert verify(
        public_key,
        "TX:lab01",
        _transaction_payload(signed_tx),
        signed_tx.sig,
    )
