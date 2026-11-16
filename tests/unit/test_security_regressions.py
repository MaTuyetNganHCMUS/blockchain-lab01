"""Security Regression Tests for P1-P5 Vulnerabilities.
Ensures issues identified in Evidence V1 are reproduced and prevented from recurring.
"""
from __future__ import annotations

import pytest

from src.common.interfaces import BlockBody, BlockHeader, Transaction
from src.common.serializer import encode_canonical
from src.crypto import generate_keypair, sign
from src.execution import (
    Ledger,
    StateEngine,
    compute_header_hash,
    compute_state_commitment,
    compute_tx_hash,
    compute_tx_root,
)


def _tx_payload(tx: Transaction) -> bytes:
    return encode_canonical(
        {
            "sender": tx.sender,
            "key": tx.key,
            "value": tx.value,
            "nonce": tx.nonce,
        }
    )


def _header_payload(header: BlockHeader) -> bytes:
    raw = BlockHeader(
        height=header.height,
        round=header.round,
        parent_hash=header.parent_hash,
        state_hash=header.state_hash,
        tx_root=header.tx_root,
        proposer=header.proposer,
        sig=b"",
    )
    return encode_canonical(raw)


def test_p1_unsigned_tx_rejected() -> None:
    """P1: Unsigned transactions, forged signatures, or wrong domain must be rejected."""
    alice_priv, alice_pub = generate_keypair()
    engine = StateEngine(public_keys={"Alice": alice_pub})

    # 1. Unsigned transaction must be rejected
    unsigned_tx = Transaction("Alice", "Alice/name", "unsigned", 1, sig=b"")
    assert not engine.apply_transaction(unsigned_tx), "Unsigned transaction must be rejected"

    # 2. Forged signature must be rejected
    forged_tx = Transaction("Alice", "Alice/name", "forged", 1, sig=b"\x00" * 64)
    assert not engine.apply_transaction(forged_tx), "Forged signature must be rejected"

    # 3. Wrong domain signature must be rejected
    unsigned_domain = Transaction("Alice", "Alice/name", "wrong_domain", 1)
    wrong_sig = sign(alice_priv, "VOTE:lab01", _tx_payload(unsigned_domain))
    wrong_domain_tx = Transaction("Alice", "Alice/name", "wrong_domain", 1, sig=wrong_sig)
    assert not engine.apply_transaction(wrong_domain_tx), "Wrong domain signature must be rejected"

    # 4. Unknown sender (public key missing in registry) must fail closed
    bob_priv, _ = generate_keypair()
    unsigned_bob = Transaction("Bob", "Bob/data", "val", 1)
    bob_sig = sign(bob_priv, "TX:lab01", _tx_payload(unsigned_bob))
    unknown_tx = Transaction("Bob", "Bob/data", "val", 1, sig=bob_sig)
    assert not engine.apply_transaction(unknown_tx), "Unknown sender without registered pubkey must be rejected"

    # 5. Positive control: valid signature under TX:lab01 must be accepted
    unsigned_valid = Transaction("Alice", "Alice/name", "AliceSmith", 1)
    valid_sig = sign(alice_priv, "TX:lab01", _tx_payload(unsigned_valid))
    valid_tx = Transaction("Alice", "Alice/name", "AliceSmith", 1, sig=valid_sig)
    assert engine.apply_transaction(valid_tx), "Valid signed transaction must be accepted"
    assert engine.get("Alice/name") == "AliceSmith"


def test_p2_distinct_transactions_distinct_hash() -> None:
    """P2: Distinct transaction tuples must not have identical computed hash."""
    t1 = Transaction("Alice", "Alice/x", "v:1", 1)
    t2 = Transaction("Alice", "Alice/x:v", "1", 1)
    assert t1 != t2, "Test precondition: transactions must be distinct objects"
    h1 = compute_tx_hash(t1)
    h2 = compute_tx_hash(t2)
    assert h1 != h2, (
        f"Ambiguous pre-hash encoding: distinct transactions produced identical hash {h1.hex()}"
    )

    # Additional cases with empty strings and separators
    t3 = Transaction("Alice", "Alice/a:b", "c", 1)
    t4 = Transaction("Alice", "Alice/a", "b:c", 1)
    assert compute_tx_hash(t3) != compute_tx_hash(t4)


def test_p3_distinct_states_distinct_commitment() -> None:
    """P3: Distinct state dictionaries must produce distinct state commitments."""
    s1 = {"Alice/x": "v:1"}
    s2 = {"Alice/x:v": "1"}
    assert s1 != s2, "Test precondition: state dictionaries must be distinct"
    c1 = compute_state_commitment(s1)
    c2 = compute_state_commitment(s2)
    assert c1 != c2, (
        f"Ambiguous state leaf encoding: distinct state dictionaries produced identical commitment {c1.hex()}"
    )

    # Additional cases with colon keys/values
    s3 = {"Alice/k": "v:1:2"}
    s4 = {"Alice/k:v": "1:2"}
    assert compute_state_commitment(s3) != compute_state_commitment(s4)


def test_p4_unsigned_header_rejected() -> None:
    """P4: Ledger.validate_block must reject unsigned or invalidly signed block headers."""
    val_priv, val_pub = generate_keypair()
    alice_priv, alice_pub = generate_keypair()

    engine = StateEngine(public_keys={"Alice": alice_pub})
    ledger = Ledger(with_genesis=True, public_keys={"Validator-1": val_pub})

    # Prepare a validly signed transaction
    unsigned_tx = Transaction("Alice", "Alice/a", "1", 1)
    tx_sig = sign(alice_priv, "TX:lab01", _tx_payload(unsigned_tx))
    tx = Transaction("Alice", "Alice/a", "1", 1, sig=tx_sig)

    # Dry-run execution
    valid, next_state_hash = engine.validate_and_compute_hash([tx])
    assert valid, "P4 setup: transaction must be valid in dry-run"

    tx_root = compute_tx_root([compute_tx_hash(tx)])
    parent_hash = ledger.get_latest_hash()

    # 1. Unsigned header must be rejected by Ledger.validate_block
    unsigned_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=next_state_hash,
        tx_root=tx_root,
        proposer="Validator-1",
        sig=b"",
    )
    body = BlockBody(header_hash=compute_header_hash(unsigned_header), transactions=[tx])
    accepted, reason = ledger.validate_block(unsigned_header, body, engine)
    assert not accepted, "Ledger.validate_block must reject an unsigned header"
    assert any(w in reason.lower() for w in ("signature", "sig", "unsigned")), (
        f"Rejection reason must be related to header signature, got: {reason}"
    )

    # 2. Forged signature on header must be rejected
    forged_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=next_state_hash,
        tx_root=tx_root,
        proposer="Validator-1",
        sig=b"\xff" * 64,
    )
    forged_body = BlockBody(header_hash=compute_header_hash(forged_header), transactions=[tx])
    accepted_forged, _ = ledger.validate_block(forged_header, forged_body, engine)
    assert not accepted_forged, "Ledger.validate_block must reject a forged header signature"

    # 3. Positive control: properly signed header must be accepted
    hdr_sig = sign(val_priv, "HEADER:lab01", _header_payload(unsigned_header))
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=next_state_hash,
        tx_root=tx_root,
        proposer="Validator-1",
        sig=hdr_sig,
    )
    signed_body = BlockBody(header_hash=compute_header_hash(signed_header), transactions=[tx])
    accepted_valid, valid_reason = ledger.validate_block(signed_header, signed_body, engine)
    assert accepted_valid, f"Ledger.validate_block must accept validly signed block: {valid_reason}"


def test_p5_failed_block_commit_is_atomic() -> None:
    """P5: Block commit must be completely atomic; state must not partially mutate if a tx fails."""
    alice_priv, alice_pub = generate_keypair()
    engine = StateEngine(public_keys={"Alice": alice_pub})

    # TX 1 is completely valid (Alice writes to Alice/a)
    u_good = Transaction("Alice", "Alice/a", "1", 1)
    sig_good = sign(alice_priv, "TX:lab01", _tx_payload(u_good))
    good_tx = Transaction("Alice", "Alice/a", "1", 1, sig=sig_good)

    # Precondition: good_tx alone must be accepted
    dry_engine = engine.clone()
    assert dry_engine.apply_transaction(good_tx), "P5 precondition: good_tx alone must be valid"

    # TX 2 is signed validly by Alice, but violates ownership (writes to Bob/b)
    u_bad = Transaction("Alice", "Bob/b", "2", 2)
    sig_bad = sign(alice_priv, "TX:lab01", _tx_payload(u_bad))
    bad_tx = Transaction("Alice", "Bob/b", "2", 2, sig=sig_bad)

    initial_hash = engine.get_state_hash()
    initial_nonce = engine.get_nonce("Alice")
    initial_data = engine.dump_data()

    # Commit block containing [good_tx, bad_tx]
    body = BlockBody(header_hash=b"probe_p5", transactions=[good_tx, bad_tx])
    with pytest.raises(ValueError):
        engine.commit_block(body)

    # Verify no partial mutation remains
    assert engine.get("Alice/a") is None, "Atomic commit violated: Alice/a was partially applied"
    assert engine.get_nonce("Alice") == initial_nonce, "Atomic commit violated: nonce was mutated"
    assert engine.dump_data() == initial_data, "Atomic commit violated: state data was mutated"
    assert engine.get_state_hash() == initial_hash, "Atomic commit violated: state hash changed"

    # Positive control: valid block with 2 valid txs commits atomically
    u_good2 = Transaction("Alice", "Alice/b", "2", 2)
    sig_good2 = sign(alice_priv, "TX:lab01", _tx_payload(u_good2))
    good_tx2 = Transaction("Alice", "Alice/b", "2", 2, sig=sig_good2)

    valid_body = BlockBody(header_hash=b"valid_p5", transactions=[good_tx, good_tx2])
    new_hash = engine.commit_block(valid_body)
    assert engine.get("Alice/a") == "1"
    assert engine.get("Alice/b") == "2"
    assert engine.get_nonce("Alice") == 2
    assert new_hash != initial_hash
