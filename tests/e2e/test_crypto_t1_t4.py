"""TV1 end-to-end scenarios T1 (invalid signatures) and T4 (replay)."""

from src.common.interfaces import BlockBody, Transaction
from src.common.mocks import MockState
from src.common.serializer import encode_canonical
from src.crypto import generate_keypair, sign, verify


def transaction_payload(tx: Transaction) -> bytes:
    return encode_canonical(
        {
            "sender": tx.sender,
            "key": tx.key,
            "value": tx.value,
            "nonce": tx.nonce,
        }
    )


def test_t1_invalid_signature_and_domain_are_rejected() -> None:
    private_key, public_key = generate_keypair()
    payload = encode_canonical({"height": 1, "block_hash": b"block-a"})
    signature = sign(private_key, "VOTE:lab01", payload)

    assert verify(public_key, "VOTE:lab01", payload, signature)
    assert not verify(public_key, "HEADER:lab01", payload, signature)
    assert not verify(public_key, "VOTE:lab01", payload + b"tampered", signature)


def test_t4_replayed_transaction_is_rejected_by_state_nonce() -> None:
    private_key, public_key = generate_keypair()
    unsigned = Transaction("Alice", "Alice/name", "Lab01", 1)
    signature = sign(private_key, "TX:lab01", transaction_payload(unsigned))
    tx = Transaction(unsigned.sender, unsigned.key, unsigned.value, unsigned.nonce, signature)

    assert verify(public_key, "TX:lab01", transaction_payload(tx), tx.sig)
    state = MockState()
    valid, _ = state.validate_and_compute_hash([tx])
    assert valid
    state.commit_block(BlockBody(b"header", (tx,)))

    replay_valid, replay_hash = state.validate_and_compute_hash([tx])
    assert replay_valid is False
    assert replay_hash == b""
