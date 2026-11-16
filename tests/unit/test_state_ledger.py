"""Unit Tests for Execution Engine, State Commitment & Ledger View.
Owner: TV2
Covering all items in Plan/job-list.txt Section 3 (TV2 - Part B).
"""
from __future__ import annotations

import hashlib
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.common.interfaces import (
    BlockBody,
    BlockHeader,
    LedgerProtocol,
    StateProtocol,
    Transaction,
)
from src.crypto import sign
from src.execution import (
    Ledger,
    State,
    StateEngine,
    compute_header_hash,
    compute_tx_hash,
    compute_tx_root,
    header_signing_payload,
    tx_signing_payload,
)


def _derive_keypair(identity: str) -> tuple[bytes, bytes]:
    seed = hashlib.sha256(f"test_seed:{identity}".encode()).digest()
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    priv_bytes = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv_bytes, pub_bytes


ALICE_PRIV, ALICE_PUB = _derive_keypair("Alice")
BOB_PRIV, BOB_PUB = _derive_keypair("Bob")
CHARLIE_PRIV, CHARLIE_PUB = _derive_keypair("Charlie")
VAL1_PRIV, VAL1_PUB = _derive_keypair("Validator-1")

TEST_KEYS = {
    "Alice": (ALICE_PRIV, ALICE_PUB),
    "Bob": (BOB_PRIV, BOB_PUB),
    "Charlie": (CHARLIE_PRIV, CHARLIE_PUB),
    "Validator-1": (VAL1_PRIV, VAL1_PUB),
}


def _make_tx(sender: str, key: str, value: str, nonce: int, chain_id: str = "lab01") -> Transaction:
    priv, _ = TEST_KEYS[sender]
    u = Transaction(sender=sender, key=key, value=value, nonce=nonce)
    sig = sign(priv, f"TX:{chain_id}", tx_signing_payload(u))
    return Transaction(sender=sender, key=key, value=value, nonce=nonce, sig=sig)


def _make_header(
    height: int,
    round: int,
    parent_hash: bytes,
    state_hash: bytes,
    tx_root: bytes,
    proposer: str,
    chain_id: str = "lab01",
) -> BlockHeader:
    priv, _ = TEST_KEYS[proposer]
    u = BlockHeader(
        height=height,
        round=round,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
        sig=b"",
    )
    sig = sign(priv, f"HEADER:{chain_id}", header_signing_payload(u))
    return BlockHeader(
        height=height,
        round=round,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
        sig=sig,
    )


class TestStateEngine(unittest.TestCase):
    """Test State Engine access control, nonce ordering and replay protection."""

    def setUp(self) -> None:
        self.public_keys = {
            "Alice": ALICE_PUB,
            "Bob": BOB_PUB,
            "Charlie": CHARLIE_PUB,
        }
        self.state = StateEngine(public_keys=self.public_keys)

    def test_protocol_compliance(self) -> None:
        """Verify StateEngine satisfies StateProtocol contract."""
        self.assertTrue(isinstance(self.state, StateProtocol))
        self.assertTrue(isinstance(State(), StateProtocol))

    def test_ownership_validation_alice_cannot_modify_bob(self) -> None:
        """Alice must NOT be allowed to write to keys not prefixed with 'Alice/'."""
        # Alice tries to write to Alice's key -> SUCCESS
        valid_tx = _make_tx(sender="Alice", key="Alice/balance", value="100", nonce=1)
        self.assertTrue(self.state.apply_transaction(valid_tx))
        self.assertEqual(self.state.get("Alice/balance"), "100")

        # Alice tries to write to Bob's key -> REJECTED
        malicious_tx = _make_tx(sender="Alice", key="Bob/balance", value="0", nonce=2)
        self.assertFalse(self.state.apply_transaction(malicious_tx))
        self.assertIsNone(self.state.get("Bob/balance"))

    def test_replay_protection_same_tx_or_nonce_rejected(self) -> None:
        """Submitting duplicate tx or replaying nonces must be strictly rejected."""
        tx1 = _make_tx(sender="Alice", key="Alice/data", value="v1", nonce=1)
        self.assertTrue(self.state.apply_transaction(tx1))

        # Replay identical tx
        self.assertFalse(self.state.apply_transaction(tx1))

        # Replay same nonce with different value
        tx2_bad_nonce = _make_tx(sender="Alice", key="Alice/data", value="v2", nonce=1)
        self.assertFalse(self.state.apply_transaction(tx2_bad_nonce))

        # Out-of-order nonces: jumping from 1 to 3
        tx3_jump_nonce = _make_tx(sender="Alice", key="Alice/data", value="v3", nonce=3)
        self.assertFalse(self.state.apply_transaction(tx3_jump_nonce))

        # Sequential nonce 2 -> SUCCESS
        tx2_correct = _make_tx(sender="Alice", key="Alice/data", value="v2", nonce=2)
        self.assertTrue(self.state.apply_transaction(tx2_correct))
        self.assertEqual(self.state.get("Alice/data"), "v2")

    def test_deterministic_state_hash_across_instances(self) -> None:
        """Two independent StateEngine instances fed with the same txs must yield identical state_hash."""
        state1 = StateEngine(public_keys=self.public_keys)
        state2 = StateEngine(public_keys=self.public_keys)

        txs = [
            _make_tx(sender="Alice", key="Alice/token", value="1000", nonce=1),
            _make_tx(sender="Bob", key="Bob/status", value="active", nonce=1),
            _make_tx(sender="Charlie", key="Charlie/role", value="validator", nonce=1),
            _make_tx(sender="Alice", key="Alice/profile", value="verified", nonce=2),
            _make_tx(sender="Bob", key="Bob/token", value="500", nonce=2),
        ]

        for tx in txs:
            state1.apply_transaction(tx)
            state2.apply_transaction(tx)

        hash1 = state1.get_state_hash()
        hash2 = state2.get_state_hash()

        self.assertEqual(hash1, hash2)
        self.assertIsInstance(hash1, bytes)
        self.assertEqual(len(hash1), 32)

    def test_dry_run_isolation_does_not_modify_base_state(self) -> None:
        """validate_and_compute_hash must not alter current committed state."""
        initial_hash = self.state.get_state_hash()
        tx = _make_tx(sender="Alice", key="Alice/test", value="xyz", nonce=1)

        valid, next_hash = self.state.validate_and_compute_hash([tx])
        self.assertTrue(valid)
        self.assertNotEqual(initial_hash, next_hash)

        # Base state should still be unchanged
        self.assertIsNone(self.state.get("Alice/test"))
        self.assertEqual(self.state.get_state_hash(), initial_hash)


class TestLedgerValidation(unittest.TestCase):
    """Test Ledger block validation and chain continuity."""

    def setUp(self) -> None:
        self.public_keys = {
            "Alice": ALICE_PUB,
            "Bob": BOB_PUB,
            "Charlie": CHARLIE_PUB,
            "Validator-1": VAL1_PUB,
        }
        self.ledger = Ledger(with_genesis=True, public_keys={"Validator-1": VAL1_PUB})
        self.state = StateEngine(public_keys=self.public_keys)

    def test_protocol_compliance(self) -> None:
        """Verify Ledger satisfies LedgerProtocol contract."""
        self.assertTrue(isinstance(self.ledger, LedgerProtocol))

    def test_genesis_block_initialized(self) -> None:
        """Ledger starts with height 0 and valid genesis header."""
        self.assertEqual(self.ledger.height, 0)
        latest = self.ledger.get_latest_header()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.height, 0)
        self.assertEqual(latest.proposer, "GENESIS")

    def test_valid_block_accepted(self) -> None:
        """A properly constructed block with correct parent and state hash must pass validation."""
        tx1 = _make_tx(sender="Alice", key="Alice/a", value="1", nonce=1)
        valid, expected_state_hash = self.state.validate_and_compute_hash([tx1])
        self.assertTrue(valid)

        tx_root = compute_tx_root([compute_tx_hash(tx1)])
        parent_hash = self.ledger.get_latest_hash()

        header = _make_header(
            height=1,
            round=0,
            parent_hash=parent_hash,
            state_hash=expected_state_hash,
            tx_root=tx_root,
            proposer="Validator-1",
        )
        body = BlockBody(header_hash=compute_header_hash(header), transactions=[tx1])

        is_valid, reason = self.ledger.validate_block(header, body, self.state)
        self.assertTrue(is_valid, msg=f"Validation failed: {reason}")

        # Commit block
        self.state.commit_block(body)
        self.ledger.add_finalized_block(header, body)

        self.assertEqual(self.ledger.height, 1)
        self.assertEqual(self.state.get("Alice/a"), "1")
        self.assertEqual(self.state.get_state_hash(), expected_state_hash)

    def test_invalid_parent_hash_rejected(self) -> None:
        """Block with arbitrary parent hash must be rejected."""
        tx1 = _make_tx(sender="Alice", key="Alice/a", value="1", nonce=1)
        _, state_hash = self.state.validate_and_compute_hash([tx1])

        bad_parent = b"\xff" * 32
        header = _make_header(
            height=1,
            round=0,
            parent_hash=bad_parent,
            state_hash=state_hash,
            tx_root=compute_tx_root([compute_tx_hash(tx1)]),
            proposer="Validator-1",
        )
        body = BlockBody(header_hash=compute_header_hash(header), transactions=[tx1])

        is_valid, reason = self.ledger.validate_block(header, body, self.state)
        self.assertFalse(is_valid)
        self.assertIn("Parent hash mismatch", reason)

    def test_invalid_state_hash_rejected(self) -> None:
        """Block with fraudulent state_hash in header must be rejected."""
        tx1 = _make_tx(sender="Alice", key="Alice/a", value="1", nonce=1)
        fake_state_hash = b"\xaa" * 32

        header = _make_header(
            height=1,
            round=0,
            parent_hash=self.ledger.get_latest_hash(),
            state_hash=fake_state_hash,
            tx_root=compute_tx_root([compute_tx_hash(tx1)]),
            proposer="Validator-1",
        )
        body = BlockBody(header_hash=compute_header_hash(header), transactions=[tx1])

        is_valid, reason = self.ledger.validate_block(header, body, self.state)
        self.assertFalse(is_valid)
        self.assertIn("State hash mismatch", reason)


if __name__ == "__main__":
    unittest.main()
