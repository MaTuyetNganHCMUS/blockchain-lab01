"""Unit tests for TV4 Consensus FSM and ValidatorNode."""

import pytest

from src.common.interfaces import (
    GENESIS_PARENT_HASH,
    MSG_BODY,
    MSG_HEADER,
    MSG_VOTE,
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    STEP_COMMIT,
    STEP_NEW_ROUND,
    STEP_PRECOMMIT,
    STEP_PREVOTE,
    STEP_PROPOSE,
    BlockBody,
    BlockHeader,
    Transaction,
    Vote,
)
from src.common.mocks import (
    MockCrypto,
    MockLedger,
    MockLockManager,
    MockLogger,
    MockNetwork,
    MockState,
    MockVoteTracker,
)
from src.consensus.codec import pack_message, unpack_message
from src.consensus.fsm import ConsensusFSM
from src.consensus.node import ValidatorNode


@pytest.fixture
def validators() -> list[str]:
    return ["node0", "node1", "node2", "node3"]


@pytest.fixture
def crypto_cluster(validators: list[str]) -> tuple[MockCrypto, dict[str, bytes], dict[str, bytes]]:
    crypto = MockCrypto()
    priv_keys = {}
    pub_keys = {}
    for val in validators:
        priv, pub = crypto.generate_keypair(val)
        priv_keys[val] = priv
        pub_keys[val] = pub
    return crypto, priv_keys, pub_keys


# ==============================================================================
# 1. DETERMINISTIC PROPOSER SELECTION TESTS
# ==============================================================================


def test_proposer_selection_modulo(validators: list[str]) -> None:
    fsm = ConsensusFSM(node_id="node0", validators=validators)
    # (H + R) % n
    # (1 + 0) % 4 = 1 -> node1
    assert fsm.get_proposer(height=1, round=0) == "node1"
    # (1 + 1) % 4 = 2 -> node2
    assert fsm.get_proposer(height=1, round=1) == "node2"
    # (1 + 2) % 4 = 3 -> node3
    assert fsm.get_proposer(height=1, round=2) == "node3"
    # (1 + 3) % 4 = 0 -> node0
    assert fsm.get_proposer(height=1, round=3) == "node0"
    # (2 + 0) % 4 = 2 -> node2
    assert fsm.get_proposer(height=2, round=0) == "node2"


def test_only_designated_proposer_can_propose(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    # At height 1, round 0, designated proposer is node1
    fsm_node0 = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm_node0.start_height(sim_time=0.0, height=1)

    # Fake proposal from node2 (not the designated proposer)
    fake_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"s",
        tx_root=b"t",
        proposer="node2",
    )
    sig = crypto.sign(priv_keys["node2"], "HEADER:lab01", crypto.encode_canonical(fake_header))
    signed_fake = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"s",
        tx_root=b"t",
        proposer="node2",
        sig=sig,
    )

    fsm_node0.on_proposal_header(sim_time=0.5, header=signed_fake)
    # Proposal must be rejected: node0 must still have prevoted_rounds empty
    assert (1, 0) not in fsm_node0.prevoted_rounds
    assert fsm_node0.step == STEP_PROPOSE


# ==============================================================================
# 2. PROPOSER CREATION & PREVOTING TESTS
# ==============================================================================


def test_designated_proposer_creates_block_and_prevotes(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    # Proposer for (H=1, R=3) is (1 + 3) % 4 = 0 -> node0
    fsm_node0 = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm_node0.height = 1
    fsm_node0.mempool.append(Transaction("Alice", "Alice/key", "1", 0))

    fsm_node0.start_round(sim_time=0.0, round=3)
    assert fsm_node0.is_current_proposer() is True

    # Check that proposal was cached
    assert (1, 3) in fsm_node0.proposals
    hdr, body = fsm_node0.proposals[(1, 3)]
    assert hdr.height == 1
    assert hdr.round == 3
    assert hdr.proposer == "node0"
    assert len(body.transactions) == 1

    # Check node0 automatically prevoted for its own proposal
    assert (1, 3) in fsm_node0.prevoted_rounds
    assert fsm_node0.step == STEP_PREVOTE
    assert len(fsm_node0.votes_cast) == 1
    assert fsm_node0.votes_cast[0].phase == PHASE_PREVOTE
    assert fsm_node0.votes_cast[0].block_hash != NIL_BLOCK_HASH


# ==============================================================================
# 3. PROPOSER SILENT / TIMEOUT & NIL PREVOTE
# ==============================================================================


def test_timeout_propose_triggers_nil_prevote(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    # At height 1, round 0, designated proposer is node1. node0 is honest validator
    fsm_node0 = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm_node0.start_height(sim_time=0.0, height=1)
    assert fsm_node0.step == STEP_PROPOSE
    assert len(fsm_node0.votes_cast) == 0

    # Simulate proposal timeout firing at sim_time = 2.0
    fsm_node0.on_timeout(sim_time=2.0, height=1, round=0, step=STEP_PROPOSE)

    # Node must advance to STEP_PREVOTE and cast PREVOTE for NIL
    assert fsm_node0.step == STEP_PREVOTE
    assert (1, 0) in fsm_node0.prevoted_rounds
    assert len(fsm_node0.votes_cast) == 1
    assert fsm_node0.votes_cast[0].phase == PHASE_PREVOTE
    assert fsm_node0.votes_cast[0].block_hash == NIL_BLOCK_HASH


# ==============================================================================
# 4. QUORUM NIL PREVOTE & ADVANCE ROUND
# ==============================================================================


def test_quorum_nil_prevotes_triggers_nil_precommit_and_round_advance(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm.start_height(sim_time=0.0, height=1)

    # Proposer times out -> prevotes NIL
    fsm.on_timeout_propose(sim_time=2.0, height=1, round=0)

    # Peer votes for NIL arrive: node1, node2
    vote_n1 = Vote(1, 0, PHASE_PREVOTE, NIL_BLOCK_HASH, "node1")
    vote_n2 = Vote(1, 0, PHASE_PREVOTE, NIL_BLOCK_HASH, "node2")

    # Feed node1's prevote NIL
    fsm.on_vote(sim_time=2.1, vote=vote_n1)
    assert fsm.step == STEP_PREVOTE  # Only 2 votes so far (node0 + node1)

    # Feed node2's prevote NIL -> reaches quorum 3 (node0, node1, node2)
    fsm.on_vote(sim_time=2.2, vote=vote_n2)

    # Must transition to PRECOMMIT and cast PRECOMMIT NIL
    assert fsm.step == STEP_PRECOMMIT
    assert (1, 0) in fsm.precommitted_rounds
    assert fsm.votes_cast[-1].phase == PHASE_PRECOMMIT
    assert fsm.votes_cast[-1].block_hash == NIL_BLOCK_HASH

    # Feed 2 more precommits for NIL
    fsm.on_vote(sim_time=2.3, vote=Vote(1, 0, PHASE_PRECOMMIT, NIL_BLOCK_HASH, "node1"))
    fsm.on_vote(sim_time=2.4, vote=Vote(1, 0, PHASE_PRECOMMIT, NIL_BLOCK_HASH, "node2"))

    # Quorum of Precommit NIL advances to Round 1!
    assert fsm.round == 1
    assert fsm.step == STEP_PROPOSE
    assert fsm.get_proposer(1, 1) == "node2"


# ==============================================================================
# 5. LOCKING (RULE 4 & 5) AND FINALIZATION
# ==============================================================================


def test_polka_locks_block_and_precommits(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm.start_height(sim_time=0.0, height=1)

    # node1 is proposer. Send valid proposal header & body
    expected_state_hash = fsm.state.compute_state_hash({})
    raw_header = BlockHeader(1, 0, GENESIS_PARENT_HASH, expected_state_hash, b"tx", "node1")
    sig = crypto.sign(priv_keys["node1"], "HEADER:lab01", crypto.encode_canonical(raw_header))
    signed_header = BlockHeader(1, 0, GENESIS_PARENT_HASH, expected_state_hash, b"tx", "node1", sig=sig)
    header_hash = crypto.hash_bytes(crypto.encode_canonical(signed_header))
    body = BlockBody(header_hash=header_hash, transactions=())

    fsm.on_proposal_header(sim_time=0.1, header=signed_header)
    fsm.on_proposal_body(sim_time=0.2, body=body)

    # Node0 prevoted for header_hash
    assert fsm.step == STEP_PREVOTE

    # Quorum of prevotes from node1 and node2
    v1 = Vote(1, 0, PHASE_PREVOTE, header_hash, "node1")
    v2 = Vote(1, 0, PHASE_PREVOTE, header_hash, "node2")
    fsm.on_vote(sim_time=0.3, vote=v1)
    fsm.on_vote(sim_time=0.4, vote=v2)

    # Rule 4: Polka achieved -> Node0 must lock on block!
    assert fsm.locked_block == header_hash
    assert fsm.locked_round == 0
    assert fsm.step == STEP_PRECOMMIT
    assert fsm.votes_cast[-1].phase == PHASE_PRECOMMIT
    assert fsm.votes_cast[-1].block_hash == header_hash

    # Now 2 precommits arrive for header_hash
    pc1 = Vote(1, 0, PHASE_PRECOMMIT, header_hash, "node1")
    pc2 = Vote(1, 0, PHASE_PRECOMMIT, header_hash, "node2")
    fsm.on_vote(sim_time=0.5, vote=pc1)
    fsm.on_vote(sim_time=0.6, vote=pc2)

    # FINALIZED!
    assert len(fsm.finalized_blocks) == 1
    assert fsm.height == 2  # Advanced to height 2
    assert fsm.round == 0   # Round reset
    assert fsm.locked_block is None  # Lock reset for new height


# ==============================================================================
# 6. RULE 1 & RULE 2 INVARIANTS
# ==============================================================================


def test_rule_1_and_rule_2_invariants(validators: list[str]) -> None:
    fsm = ConsensusFSM(node_id="node0", validators=validators)
    fsm.start_height(sim_time=0.0, height=1)

    # Attempt to cast prevote twice
    v1 = fsm.cast_vote(sim_time=0.1, phase=PHASE_PREVOTE, block_hash=b"blockA")
    assert (1, 0) in fsm.prevoted_rounds

    # Try triggering another prevote via proposal
    hdr = BlockHeader(1, 0, GENESIS_PARENT_HASH, b"state", b"tx", "node1")
    fsm._evaluate_proposal_and_prevote(sim_time=0.2, header=hdr, body=None)

    # Prevoted count must remain 1
    prevotes = [v for v in fsm.votes_cast if v.phase == PHASE_PREVOTE]
    assert len(prevotes) == 1

    # Attempt to cast precommit twice
    fsm.cast_vote(sim_time=0.3, phase=PHASE_PRECOMMIT, block_hash=b"blockA")
    assert (1, 0) in fsm.precommitted_rounds

    fsm.on_timeout_prevote(sim_time=0.4, height=1, round=0)
    precommits = [v for v in fsm.votes_cast if v.phase == PHASE_PRECOMMIT]
    assert len(precommits) == 1


# ==============================================================================
# 7. VALIDATOR NODE LIFECYCLE TESTS
# ==============================================================================


def test_validator_node_integration(validators: list[str]) -> None:
    node = ValidatorNode(node_id="node0", validators=validators)
    assert node.height == 1
    assert node.round == 0
    assert node.is_validator is True

    node.start(sim_time=0.0)
    assert node.step in (STEP_NEW_ROUND, STEP_PROPOSE)

    tx = Transaction("Alice", "Alice/msg", "hello", 1)
    assert node.submit_transaction(tx) is True
    assert len(node.fsm.mempool) == 1


# ==============================================================================
# 8. CODEC ROUND-TRIP TESTS
# ==============================================================================


def test_codec_message_round_trip() -> None:
    tx = Transaction("Alice", "Alice/msg", "hello", 1, b"sig1")
    hdr = BlockHeader(1, 0, GENESIS_PARENT_HASH, b"s"*32, b"t"*32, "node0", b"sig2")
    body = BlockBody(b"h"*32, (tx,))
    vote = Vote(1, 0, PHASE_PREVOTE, b"b"*32, "node1", b"sig3")

    # Header
    raw_hdr = pack_message(MSG_HEADER, hdr)
    t_hdr, res_hdr = unpack_message(raw_hdr)
    assert t_hdr == MSG_HEADER
    assert res_hdr == hdr

    # Body
    raw_body = pack_message(MSG_BODY, body)
    t_body, res_body = unpack_message(raw_body)
    assert t_body == MSG_BODY
    assert res_body == body

    # Vote
    raw_vote = pack_message(MSG_VOTE, vote)
    t_vote, res_vote = unpack_message(raw_vote)
    assert t_vote == MSG_VOTE
    assert res_vote == vote


# ==============================================================================
# 9. COMPREHENSIVE RULE & LIVENESS TESTS
# ==============================================================================


def test_reject_proposal_with_invalid_state_hash(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm.start_height(sim_time=0.0, height=1)

    # Proposer node1 sends proposal with fabricated state hash
    bad_state_hash = b"\xff" * 32
    raw_header = BlockHeader(1, 0, GENESIS_PARENT_HASH, bad_state_hash, b"tx", "node1")
    sig = crypto.sign(priv_keys["node1"], "HEADER:lab01", crypto.encode_canonical(raw_header))
    signed_header = BlockHeader(1, 0, GENESIS_PARENT_HASH, bad_state_hash, b"tx", "node1", sig=sig)
    header_hash = crypto.hash_bytes(crypto.encode_canonical(signed_header))
    body = BlockBody(header_hash=header_hash, transactions=())

    fsm.on_proposal_header(sim_time=0.1, header=signed_header)
    fsm.on_proposal_body(sim_time=0.2, body=body)

    # Rule 3: Bad state hash must cause node to prevote NIL
    assert fsm.step == STEP_PREVOTE
    assert len(fsm.votes_cast) == 1
    assert fsm.votes_cast[0].block_hash == NIL_BLOCK_HASH


def test_reject_proposal_with_invalid_parent_hash(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm.start_height(sim_time=0.0, height=1)

    expected_state = fsm.state.compute_state_hash({})
    bad_parent = b"\xee" * 32
    raw_header = BlockHeader(1, 0, bad_parent, expected_state, b"tx", "node1")
    sig = crypto.sign(priv_keys["node1"], "HEADER:lab01", crypto.encode_canonical(raw_header))
    signed_header = BlockHeader(1, 0, bad_parent, expected_state, b"tx", "node1", sig=sig)
    header_hash = crypto.hash_bytes(crypto.encode_canonical(signed_header))
    body = BlockBody(header_hash=header_hash, transactions=())

    fsm.on_proposal_header(sim_time=0.1, header=signed_header)
    fsm.on_proposal_body(sim_time=0.2, body=body)

    # Rule 3: Bad parent must cause node to prevote NIL
    assert fsm.step == STEP_PREVOTE
    assert fsm.votes_cast[0].block_hash == NIL_BLOCK_HASH


def test_reject_vote_with_invalid_signature(
    validators: list[str],
    crypto_cluster: tuple[MockCrypto, dict[str, bytes], dict[str, bytes]],
) -> None:
    crypto, priv_keys, pub_keys = crypto_cluster
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        crypto=crypto,
        priv_key=priv_keys["node0"],
        pub_keys=pub_keys,
    )
    fsm.start_height(sim_time=0.0, height=1)

    # Vote from node1 with corrupted signature
    bad_vote = Vote(1, 0, PHASE_PREVOTE, NIL_BLOCK_HASH, "node1", sig=b"corrupted_sig")
    fsm.on_vote(sim_time=0.5, vote=bad_vote)

    # Vote tracker must not record this corrupted vote
    step_key = (1, 0, PHASE_PREVOTE)
    assert step_key not in fsm.vote_tracker.votes_by_step or "node1" not in fsm.vote_tracker.votes_by_step[step_key]


def test_progressive_timeout_backoff(validators: list[str]) -> None:
    fsm = ConsensusFSM(
        node_id="node0",
        validators=validators,
        timeout_propose=2.0,
        timeout_delta=0.5,
    )
    # tau(R) = base + R * delta
    assert fsm.get_timeout_delay(fsm.timeout_propose, round=0) == 2.0
    assert fsm.get_timeout_delay(fsm.timeout_propose, round=1) == 2.5
    assert fsm.get_timeout_delay(fsm.timeout_propose, round=2) == 3.0
    assert fsm.get_timeout_delay(fsm.timeout_propose, round=4) == 4.0
