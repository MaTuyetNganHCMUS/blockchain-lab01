"""Unit tests verifying interface definitions, data models, protocols, and mocks."""

import pytest

from src.common.interfaces import (
    DOMAIN_HEADER,
    DOMAIN_TX,
    DOMAIN_VOTE,
    EVENT_SEND,
    GENESIS_PARENT_HASH,
    MSG_HEADER,
    MSG_VOTE,
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    STEP_PREVOTE,
    STEP_PROPOSE,
    BlockBody,
    BlockHeader,
    ConsensusProtocol,
    CryptoProtocol,
    LedgerProtocol,
    LockManagerProtocol,
    LoggerProtocol,
    NetPacket,
    NetworkProtocol,
    QuorumResult,
    StateProtocol,
    Transaction,
    Vote,
    VoteTrackerProtocol,
    select_proposer,
)
from src.common.mocks import (
    MockConsensus,
    MockCrypto,
    MockLedger,
    MockLockManager,
    MockLogger,
    MockNetwork,
    MockState,
    MockVoteTracker,
)
from src.consensus.fsm import ConsensusFSM
from src.consensus.node import ValidatorNode
from src.interfaces import Transaction as RootTransaction


# ==============================================================================
# 1. DATA MODEL TESTS (IMMUTABILITY & NORMALIZATION)
# ==============================================================================


def test_transaction_contract() -> None:
    tx = Transaction(sender="Alice", key="Alice/balance", value="100", nonce=1, sig=b"sig")
    assert tx.sender == "Alice"
    assert tx.key == "Alice/balance"
    assert tx.value == "100"
    assert tx.nonce == 1
    assert tx.sig == b"sig"

    # Enforce immutability
    with pytest.raises(AttributeError):
        tx.nonce = 2  # type: ignore[misc]


def test_root_and_common_interfaces_consistency() -> None:
    # Verifies both import paths refer to identical contracts
    assert Transaction is RootTransaction


def test_block_header_contract() -> None:
    header = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"state1",
        tx_root=b"tx1",
        proposer="node0",
    )
    assert header.height == 1
    assert header.round == 0
    assert header.parent_hash == GENESIS_PARENT_HASH
    assert header.sig == b""

    with pytest.raises(AttributeError):
        header.height = 2  # type: ignore[misc]


def test_block_body_normalization() -> None:
    tx1 = Transaction("Alice", "Alice/msg", "hello", 1)
    tx2 = Transaction("Bob", "Bob/msg", "world", 1)
    # Pass a mutable list; BlockBody must normalize to an immutable tuple
    body = BlockBody(header_hash=b"h1", transactions=[tx1, tx2])
    assert isinstance(body.transactions, tuple)
    assert len(body.transactions) == 2
    assert body.transactions[0] == tx1


def test_vote_contract() -> None:
    vote = Vote(
        height=10,
        round=2,
        phase=PHASE_PREVOTE,
        block_hash=NIL_BLOCK_HASH,
        voter="val1",
        sig=b"sig_bytes",
    )
    assert vote.height == 10
    assert vote.round == 2
    assert vote.phase == "PREVOTE"
    assert vote.block_hash == b"NIL"
    assert vote.voter == "val1"


def test_quorum_result_contract() -> None:
    v1 = Vote(1, 0, PHASE_PREVOTE, b"hashA", "node1")
    v2 = Vote(1, 0, PHASE_PREVOTE, b"hashA", "node2")
    res = QuorumResult(has_quorum=True, block_hash=b"hashA", round=0, phase=PHASE_PREVOTE, votes=[v1, v2])
    assert res.has_quorum is True
    assert isinstance(res.votes, tuple)
    assert len(res.votes) == 2


# ==============================================================================
# 2. RUNTIME PROTOCOL CONFORMANCE TESTS
# ==============================================================================


def test_protocols_runtime_checkable() -> None:
    crypto = MockCrypto()
    state = MockState()
    ledger = MockLedger()
    network = MockNetwork()
    consensus = MockConsensus()
    vote_tracker = MockVoteTracker()
    lock_mgr = MockLockManager()
    logger = MockLogger()
    fsm = ConsensusFSM(node_id="node0", validators=["node0", "node1"])

    assert isinstance(crypto, CryptoProtocol)
    assert isinstance(state, StateProtocol)
    assert isinstance(ledger, LedgerProtocol)
    assert isinstance(network, NetworkProtocol)
    assert isinstance(consensus, ConsensusProtocol)
    assert isinstance(vote_tracker, VoteTrackerProtocol)
    assert isinstance(lock_mgr, LockManagerProtocol)
    assert isinstance(logger, LoggerProtocol)
    assert isinstance(fsm, ConsensusProtocol)


# ==============================================================================
# 3. CRYPTO & CANONICAL SERIALIZATION MOCK TESTS
# ==============================================================================


def test_mock_crypto_deterministic_encoding() -> None:
    crypto = MockCrypto()

    # Dictionary sorting test: different insertion order must yield identical bytes
    dict_a = {"z": 100, "a": "hello", "m": [1, 2, 3]}
    dict_b = {"a": "hello", "m": [1, 2, 3], "z": 100}
    assert crypto.encode_canonical(dict_a) == crypto.encode_canonical(dict_b)

    # SHA-256 length check
    h = crypto.hash_bytes(b"payload")
    assert len(h) == 32


def test_mock_crypto_signatures_and_domain_separation() -> None:
    crypto = MockCrypto()
    priv, pub = crypto.generate_keypair("Alice")
    payload = b"state_change_123"

    sig_tx = crypto.sign(priv, DOMAIN_TX, payload)
    assert crypto.verify(pub, DOMAIN_TX, payload, sig_tx) is True

    # Domain Separation: signature under TX must fail under VOTE or HEADER
    assert crypto.verify(pub, DOMAIN_VOTE, payload, sig_tx) is False
    assert crypto.verify(pub, DOMAIN_HEADER, payload, sig_tx) is False

    # Payload mutation test
    assert crypto.verify(pub, DOMAIN_TX, payload + b"x", sig_tx) is False


# ==============================================================================
# 4. STATE & LEDGER MOCK TESTS
# ==============================================================================


def test_mock_state_ownership_and_replay_protection() -> None:
    state = MockState()

    # Valid tx: Alice updates Alice/status
    valid_tx = Transaction(sender="Alice", key="Alice/status", value="active", nonce=0)
    ok, hash_val = state.validate_and_compute_hash([valid_tx])
    assert ok is True
    assert len(hash_val) == 32

    # Invalid tx 1: Alice updates Bob/status (ownership violation)
    invalid_ownership = Transaction(sender="Alice", key="Bob/status", value="hacked", nonce=0)
    ok, _ = state.validate_and_compute_hash([invalid_ownership])
    assert ok is False

    # Commit valid tx
    state.commit_block(BlockBody(header_hash=b"h1", transactions=[valid_tx]))
    assert state.values["Alice/status"] == "active"

    # Invalid tx 2: Replay same nonce
    replayed_tx = Transaction(sender="Alice", key="Alice/status", value="idle", nonce=0)
    ok, _ = state.validate_and_compute_hash([replayed_tx])
    assert ok is False

    # Higher nonce succeeds
    next_tx = Transaction(sender="Alice", key="Alice/status", value="idle", nonce=1)
    ok, _ = state.validate_and_compute_hash([next_tx])
    assert ok is True


def test_mock_ledger() -> None:
    ledger = MockLedger()
    header = BlockHeader(1, 0, GENESIS_PARENT_HASH, b"state", b"tx", "proposer")
    body = BlockBody(header_hash=b"hdr_hash_1", transactions=())

    ledger.add_finalized_block(header, body)
    assert ledger.is_finalized(1, b"hdr_hash_1") is True
    assert ledger.is_finalized(1, b"wrong_hash") is False
    assert ledger.get_by_height(1) == (header, body)


# ==============================================================================
# 5. NETWORK & CONSENSUS MOCK TESTS
# ==============================================================================


def test_mock_network_and_consensus() -> None:
    net = MockNetwork()
    net.send("node0", "node1", b"raw_msg")
    assert len(net.sent_packets) == 1
    assert net.sent_packets[0].sender == "node0"
    assert net.sent_packets[0].receiver == "node1"

    net.broadcast_header("node0", b"hdr_bytes")
    assert len(net.broadcast_headers) == 1

    net.schedule_timeout("node0", 2.5, "PROPOSE_TIMEOUT")
    assert len(net.scheduled_timeouts) == 1
    assert net.scheduled_timeouts[0] == ("node0", 2.5, "PROPOSE_TIMEOUT")

    consensus = MockConsensus()
    consensus.on_packet(1.0, b"packet_data")
    assert len(consensus.received_packets) == 1
    consensus.on_timeout(2.5, 1, 0, STEP_PROPOSE)
    assert len(consensus.received_timeouts) == 1


# ==============================================================================
# 6. VOTE TRACKER & LOCK MANAGER (RULE 4, 5, 7) TESTS
# ==============================================================================


def test_mock_vote_tracker_quorum_and_deduplication() -> None:
    # 4 validators -> f = 1 -> quorum = 2f + 1 = 3
    tracker = MockVoteTracker(validator_count=4)
    v1 = Vote(1, 0, PHASE_PREVOTE, b"block_A", "node0")
    v2 = Vote(1, 0, PHASE_PREVOTE, b"block_A", "node1")
    v3 = Vote(1, 0, PHASE_PREVOTE, b"block_A", "node2")

    # Vote 1
    r1 = tracker.add_vote(v1)
    assert r1.has_quorum is False

    # Rule 7: Duplicate vote from node0 must be ignored and not trigger quorum
    r_dup = tracker.add_vote(v1)
    assert r_dup.has_quorum is False

    # Vote 2
    r2 = tracker.add_vote(v2)
    assert r2.has_quorum is False

    # Vote 3 reaches 3 votes -> quorum achieved!
    r3 = tracker.add_vote(v3)
    assert r3.has_quorum is True
    assert r3.block_hash == b"block_A"
    assert len(r3.votes) == 3


def test_mock_lock_manager_rules() -> None:
    lock_mgr = MockLockManager()

    # Rule 4: Not locked yet -> can prevote any block
    assert lock_mgr.can_prevote(b"block_A", round=0, polka_round=-1) is True

    # Lock on block_A at round 0
    lock_mgr.record_lock(b"block_A", round=0)
    assert lock_mgr.locked_block == b"block_A"
    assert lock_mgr.locked_round == 0

    # Locked on block_A -> can prevote block_A at round 1
    assert lock_mgr.can_prevote(b"block_A", round=1, polka_round=-1) is True

    # Cannot prevote block_B if polka_round <= locked_round (0)
    assert lock_mgr.can_prevote(b"block_B", round=1, polka_round=0) is False

    # Rule 5 (PoLC): Can prevote block_B if polka_round > locked_round (e.g. polka at round 1)
    assert lock_mgr.can_prevote(b"block_B", round=2, polka_round=1) is True


def test_mock_logger() -> None:
    logger = MockLogger()
    logger.log(0.12, "node1", EVENT_SEND, 1, 0, "Packet dispatched")
    assert len(logger.records) == 1
    lines = logger.formatted_lines()
    assert len(lines) == 1
    assert "[00000.12] node=node1 event=SEND H=1 R=0 detail=Packet dispatched" == lines[0]


# ==============================================================================
# 7. PROPOSER SELECTION & VALIDATOR NODE WIRING
# ==============================================================================


def test_select_proposer() -> None:
    vals = ["n0", "n1", "n2", "n3"]
    # (1 + 0) % 4 = 1 -> "n1"
    assert select_proposer(1, 0, vals) == "n1"
    # (1 + 1) % 4 = 2 -> "n2"
    assert select_proposer(1, 1, vals) == "n2"
    # (2 + 2) % 4 = 0 -> "n0"
    assert select_proposer(2, 2, vals) == "n0"

    with pytest.raises(ValueError):
        select_proposer(1, 0, [])


def test_validator_node_initialization() -> None:
    vals = ["node0", "node1", "node2", "node3"]
    node = ValidatorNode(node_id="node0", validators=vals)
    assert node.node_id == "node0"
    assert isinstance(node.crypto, CryptoProtocol)
    assert isinstance(node.state, StateProtocol)
    assert isinstance(node.ledger, LedgerProtocol)
    assert isinstance(node.network, NetworkProtocol)
    assert isinstance(node.vote_tracker, VoteTrackerProtocol)
    assert isinstance(node.lock_manager, LockManagerProtocol)
    assert isinstance(node.logger, LoggerProtocol)
    assert isinstance(node.fsm, ConsensusProtocol)
