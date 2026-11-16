"""Unit and integration tests for L01-L04, L07-L09, and M01-M14 security/pipeline guarantees."""

from __future__ import annotations

import itertools

import pytest

from src.common.interfaces import (
    DOMAIN_HEADER,
    DOMAIN_TX,
    DOMAIN_VOTE,
    EVENT_REJECT_BODY,
    EVENT_REJECT_HEADER,
    EVENT_REJECT_PACKET,
    EVENT_REJECT_TX,
    EVENT_REJECT_VOTE,
    MSG_BODY,
    MSG_HEADER,
    NIL_BLOCK_HASH,
    PHASE_PREVOTE,
    REASON_INVALID_SIGNATURE,
    REASON_MALFORMED_PACKET,
    REASON_MEMPOOL_FULL,
    REASON_OVERSIZED_BLOCK,
    REASON_TX_BAD_SIGNATURE,
    REASON_TX_DUPLICATE,
    REASON_TX_NONCE_INVALID,
    REASON_TX_TOO_LARGE,
    REASON_TX_UNAUTHORIZED_KEY,
    REASON_UNACCEPTED_HEADER,
    REASON_WRONG_PROPOSER,
    BlockBody,
    BlockHeader,
    BlockPolicy,
    Transaction,
    Vote,
)
from src.consensus.codec import pack_message
from src.consensus.node import ValidatorNode
from src.crypto import sign
from src.execution import (
    Mempool,
    MempoolConfig,
    StateEngine,
    compute_header_hash,
    compute_tx_hash,
    compute_tx_root,
    header_signing_payload,
    tx_signing_payload,
)
from src.security import VoteTracker, validate_bft_validator_set, vote_signing_bytes
from src.simulation import build_integrated_cluster, derive_deterministic_keypair, sign_tx

# ==============================================================================
# L08: BFT VALIDATOR SET & QUORUM INTERSECTION
# ==============================================================================


@pytest.mark.parametrize("bad_n", [1, 2, 3, 5, 6, 8, 9])
def test_rejects_non_bft_validator_counts(bad_n: int) -> None:
    vals = [f"node{i}" for i in range(bad_n)]
    with pytest.raises(ValueError, match="3f \\+ 1"):
        validate_bft_validator_set(vals)
    with pytest.raises(ValueError, match="3f \\+ 1"):
        VoteTracker(validators=vals)
    with pytest.raises(ValueError, match="3f \\+ 1"):
        ValidatorNode.create_integrated("node0", vals)


def test_rejects_unsafe_custom_quorum_threshold() -> None:
    vals = [f"node{i}" for i in range(10)]
    with pytest.raises(ValueError, match="BFT safety"):
        VoteTracker(validators=vals, quorum_threshold=6)
    tracker = VoteTracker(validators=vals)
    assert tracker.f == 3
    assert tracker.quorum == 7


@pytest.mark.parametrize("n", [4, 7, 10])
def test_quorum_intersection_holds_for_valid_bft_sizes(n: int) -> None:
    vals = [f"node{i}" for i in range(n)]
    f, q = validate_bft_validator_set(vals)
    assert n == 3 * f + 1
    assert q == 2 * f + 1
    byzantine = set(vals[:f])
    honest = set(vals[f:])
    # Exhaustive check for n <= 10: every two quorums share >= 1 honest validator
    for q1 in itertools.combinations(vals, q):
        s1 = set(q1)
        for q2 in itertools.combinations(vals, q):
            overlap_honest = (s1 & set(q2)) & honest
            assert len(overlap_honest) >= 1, f"Missing honest overlap for {s1} & {q2} (byz={byzantine})"


# ==============================================================================
# L01 & L02 (M01-M07): TRANSACTION ADMISSION & BOUNDED MEMPOOL
# ==============================================================================


def test_admission_rejects_bad_signature_ownership_duplicate_size_and_quota() -> None:
    alice_priv, alice_pub = derive_deterministic_keypair("m01-alice")
    bob_priv, bob_pub = derive_deterministic_keypair("m01-bob")
    state = StateEngine(public_keys={"Alice": alice_pub, "Bob": bob_pub})
    mempool = Mempool(
        MempoolConfig(
            max_txs=2,
            max_bytes=500,
            max_tx_bytes=260,
            future_nonce_window=2,
        )
    )

    # M01: bad signature / wrong domain
    unsigned = Transaction("Alice", "Alice/a", "1", 1)
    bad_sig_tx = Transaction("Alice", "Alice/a", "1", 1, sig=b"\x00" * 64)
    wrong_domain_sig = sign(alice_priv, f"{DOMAIN_HEADER}:lab01", tx_signing_payload(unsigned))
    wrong_domain_tx = Transaction("Alice", "Alice/a", "1", 1, sig=wrong_domain_sig)
    assert mempool.admit(bad_sig_tx, state=state).reason_code == REASON_TX_BAD_SIGNATURE
    assert mempool.admit(wrong_domain_tx, state=state).reason_code == REASON_TX_BAD_SIGNATURE

    # M02: ownership violation
    bad_owner_u = Transaction("Alice", "Bob/a", "1", 1)
    bad_owner = Transaction("Alice", "Bob/a", "1", 1, sig=sign(alice_priv, f"{DOMAIN_TX}:lab01", tx_signing_payload(bad_owner_u)))
    assert mempool.admit(bad_owner, state=state).reason_code == REASON_TX_UNAUTHORIZED_KEY

    # M06: tx too large
    huge_u = Transaction("Alice", "Alice/big", "x" * 300, 1)
    huge_tx = Transaction("Alice", "Alice/big", "x" * 300, 1, sig=sign(alice_priv, f"{DOMAIN_TX}:lab01", tx_signing_payload(huge_u)))
    assert mempool.admit(huge_tx, state=state).reason_code == REASON_TX_TOO_LARGE

    # M05: far future nonce rejected by policy
    far_u = Transaction("Alice", "Alice/far", "1", 10)
    far_tx = Transaction("Alice", "Alice/far", "1", 10, sig=sign(alice_priv, f"{DOMAIN_TX}:lab01", tx_signing_payload(far_u)))
    assert mempool.admit(far_tx, state=state).reason_code == REASON_TX_NONCE_INVALID

    # M03: 100 duplicate submissions -> stored once
    tx1 = sign_tx("Alice", alice_priv, "Alice/k1", "v1", 1)
    assert mempool.admit(tx1, state=state, sim_time=1.0).accepted is True
    for _ in range(99):
        dup_res = mempool.admit(tx1, state=state, sim_time=1.0)
        assert dup_res.accepted is False
        assert dup_res.reason_code == REASON_TX_DUPLICATE
    assert len(mempool) == 1

    # M07: max_txs quota enforced
    tx2 = sign_tx("Bob", bob_priv, "Bob/k1", "v1", 1)
    assert mempool.admit(tx2, state=state, sim_time=1.0).accepted is True
    tx3 = sign_tx("Alice", alice_priv, "Alice/k2", "v2", 2)
    full_res = mempool.admit(tx3, state=state, sim_time=1.0)
    assert full_res.accepted is False
    assert full_res.reason_code == REASON_MEMPOOL_FULL

    # M04: finalized tx cannot be re-admitted
    state.commit_block(BlockBody(header_hash=b"h1", transactions=(tx1,)))
    mempool.remove_finalized((tx1,), state=state)
    assert len(mempool) == 1
    replay_res = mempool.admit(tx1, state=state, sim_time=2.0)
    assert replay_res.accepted is False
    assert replay_res.reason_code in (REASON_TX_DUPLICATE, REASON_TX_NONCE_INVALID)


def test_mempool_ttl_eviction_and_byte_limit() -> None:
    alice_priv, alice_pub = derive_deterministic_keypair("ttl-alice")
    state = StateEngine(public_keys={"Alice": alice_pub})
    tx1 = sign_tx("Alice", alice_priv, "Alice/a", "1", 1)
    tx2 = sign_tx("Alice", alice_priv, "Alice/b", "2", 2)
    mempool = Mempool(MempoolConfig(max_txs=10, max_bytes=300, max_tx_bytes=300, tx_ttl=5.0))
    assert mempool.admit(tx1, state=state, sim_time=0.0).accepted is True
    # Byte quota blocks second tx at t=1.0
    assert mempool.admit(tx2, state=state, sim_time=1.0).reason_code == REASON_MEMPOOL_FULL
    # At sim_time=6.0, tx1 expires deterministically allowing tx2
    assert mempool.admit(tx2, state=state, sim_time=6.0).accepted is True
    assert len(mempool) == 1


def test_local_and_network_tx_admission_consistency() -> None:
    _validators, nodes, _, logger, _, _users = build_integrated_cluster(validator_count=4)
    node0 = nodes["node0"]
    node1 = nodes["node1"]
    bad_tx = Transaction("Alice", "Alice/hack", "1", 1, sig=b"\x02" * 64)
    assert node0.submit_transaction(bad_tx, sim_time=0.1) is False
    node1.handle_packet(0.2, pack_message("TX", bad_tx), sender="node0")
    assert len(node0.fsm.mempool) == 0
    assert len(node1.fsm.mempool) == 0
    tx_rejects = [r for r in logger.records if r.event_type == EVENT_REJECT_TX]
    assert len(tx_rejects) == 2
    assert all(REASON_TX_BAD_SIGNATURE in r.detail for r in tx_rejects)


# ==============================================================================
# L04 (M10-M11): BLOCK SIZE ENFORCEMENT ON PROPOSER & VALIDATOR
# ==============================================================================


def test_block_policy_limits_proposal_and_validator_rejects_oversized() -> None:
    policy = BlockPolicy(max_txs=2, max_bytes=2048)
    _validators, nodes, _, logger, priv_keys, users = build_integrated_cluster(
        validator_count=4,
        block_policy=policy,
    )
    proposer = nodes["node1"]  # proposer at H=1, R=0
    tx1 = sign_tx("Alice", users["Alice"], "Alice/k1", "1", 1)
    tx2 = sign_tx("Alice", users["Alice"], "Alice/k2", "2", 2)
    tx3 = sign_tx("Alice", users["Alice"], "Alice/k3", "3", 3)
    for tx in (tx1, tx2, tx3):
        assert proposer.submit_transaction(tx)

    proposer.start(0.0)
    _hdr, body = proposer.fsm.proposals[(1, 0)]
    assert len(body.transactions) == 2
    assert len(proposer.fsm.mempool) == 3  # tx3 preserved until finalized

    # M11: validator rejects an oversized proposal directly
    receiver = nodes["node0"]
    receiver.start(0.0)
    oversized_txs = (tx1, tx2, tx3)
    _, state_h = receiver.state.validate_and_compute_hash(oversized_txs)
    tx_root = compute_tx_root([compute_tx_hash(t) for t in oversized_txs])
    unsigned_hdr = BlockHeader(1, 0, receiver.ledger.get_latest_hash(), state_h, tx_root, "node1")
    hdr_sig = sign(priv_keys["node1"], f"{DOMAIN_HEADER}:lab01", header_signing_payload(unsigned_hdr))
    bad_hdr = BlockHeader(1, 0, unsigned_hdr.parent_hash, state_h, tx_root, "node1", sig=hdr_sig)
    bad_hash = compute_header_hash(bad_hdr)
    bad_body = BlockBody(bad_hash, oversized_txs)

    receiver.fsm.on_proposal_header(0.1, bad_hdr)
    receiver.fsm.on_proposal_body(0.2, bad_body)
    assert receiver.fsm.votes_cast[-1].block_hash == NIL_BLOCK_HASH
    assert any(
        r.event_type == EVENT_REJECT_BODY and REASON_OVERSIZED_BLOCK in r.detail
        for r in logger.records
    )


# ==============================================================================
# L07 & L09 (M12-M13): HEADER-BEFORE-BODY & REJECTION LOGGING
# ==============================================================================


def test_unsolicited_body_and_invalid_header_rejected_with_logs() -> None:
    _validators, nodes, sim, logger, _priv_keys, users = build_integrated_cluster(validator_count=4)
    node0 = nodes["node0"]
    node0.start(0.0)

    # M13: Body arriving before accepted header MUST be rejected (not stashed in unlinked_bodies)
    tx = sign_tx("Alice", users["Alice"], "Alice/a", "1", 1)
    unsolicited = BlockBody(header_hash=b"\x99" * 32, transactions=(tx,))
    sent_before = sim.total_sent
    node0.handle_packet(0.1, pack_message(MSG_BODY, unsolicited), sender="node1")
    assert b"\x99" * 32 not in node0.fsm.unlinked_bodies
    assert b"\x99" * 32 not in node0.fsm.block_cache
    assert sim.total_sent == sent_before
    assert any(
        r.event_type == EVENT_REJECT_BODY and REASON_UNACCEPTED_HEADER in r.detail
        for r in logger.records
    )

    # M12: Header with wrong proposer / bad signature does NOT trigger BODY_REQUEST
    wrong_proposer_hdr = BlockHeader(
        1, 0, node0.ledger.get_latest_hash(), node0.state.get_state_hash(),
        compute_tx_root([]), "node2", sig=b"\x01" * 64,
    )
    node0.handle_packet(0.2, pack_message(MSG_HEADER, wrong_proposer_hdr), sender="node2")

    bad_sig_hdr = BlockHeader(
        1, 0, node0.ledger.get_latest_hash(), node0.state.get_state_hash(),
        compute_tx_root([]), "node1", sig=b"\x01" * 64,
    )
    node0.handle_packet(0.3, pack_message(MSG_HEADER, bad_sig_hdr), sender="node1")
    assert sim.total_sent == sent_before

    # Wrong domain vote & malformed packet logged
    bad_vote = Vote(1, 0, PHASE_PREVOTE, NIL_BLOCK_HASH, "node1", sig=b"\x01" * 64)
    node0.handle_packet(0.4, pack_message("VOTE", bad_vote), sender="node1")
    node0.handle_packet(0.5, b"{bad-json", sender="node1")

    seen_events = {r.event_type for r in logger.records}
    assert {EVENT_REJECT_BODY, EVENT_REJECT_HEADER, EVENT_REJECT_VOTE, EVENT_REJECT_PACKET}.issubset(seen_events)
    assert any(REASON_WRONG_PROPOSER in r.detail for r in logger.records if r.event_type == EVENT_REJECT_HEADER)
    assert any(REASON_INVALID_SIGNATURE in r.detail for r in logger.records if r.event_type == EVENT_REJECT_HEADER)
    assert any(REASON_INVALID_SIGNATURE in r.detail for r in logger.records if r.event_type == EVENT_REJECT_VOTE)
    assert any(REASON_MALFORMED_PACKET in r.detail for r in logger.records if r.event_type == EVENT_REJECT_PACKET)


def test_reproposed_locked_block_across_round_change_prevotes_cleanly() -> None:
    _validators, nodes, _sim, _, priv_keys, _ = build_integrated_cluster(validator_count=4)
    node0 = nodes["node0"]
    node0.start(0.0)

    # Create valid R=0 proposal from node1 (proposer for H=1, R=0)
    parent = node0.ledger.get_latest_hash()
    state_h = node0.state.get_state_hash()
    tx_root = compute_tx_root([])
    u_hdr0 = BlockHeader(1, 0, parent, state_h, tx_root, "node1")
    sig0 = sign(priv_keys["node1"], f"{DOMAIN_HEADER}:lab01", header_signing_payload(u_hdr0))
    hdr0 = BlockHeader(1, 0, parent, state_h, tx_root, "node1", sig=sig0)
    h0 = compute_header_hash(hdr0)
    body0 = BlockBody(h0, ())

    node0.fsm.on_proposal_header(0.1, hdr0, sender="node1")
    node0.fsm.on_proposal_body(0.2, body0)
    for voter in ("node1", "node2"):
        uv = Vote(1, 0, PHASE_PREVOTE, h0, voter)
        sv = Vote(1, 0, PHASE_PREVOTE, h0, voter, sig=sign(priv_keys[voter], f"{DOMAIN_VOTE}:lab01", vote_signing_bytes(uv)))
        node0.fsm.on_vote(0.3, sv)

    assert node0.locked_block == h0
    assert node0.locked_round == 0

    # Round 0 precommit times out -> advance to Round 1 (proposer=node2)
    node0.fsm.on_timeout_precommit(2.0, 1, 0)
    assert node0.round == 1

    # node2 re-proposes the identical block content at Round 1
    u_hdr1 = BlockHeader(1, 1, parent, state_h, tx_root, "node2")
    sig1 = sign(priv_keys["node2"], f"{DOMAIN_HEADER}:lab01", header_signing_payload(u_hdr1))
    hdr1 = BlockHeader(1, 1, parent, state_h, tx_root, "node2", sig=sig1)
    h1 = compute_header_hash(hdr1)
    body1 = BlockBody(h1, ())
    assert h1 != h0  # Header hash differs due to round/proposer

    node0.fsm.on_proposal_header(2.1, hdr1, sender="node2")
    node0.fsm.on_proposal_body(2.2, body1)
    assert (1, 1) in node0.fsm.prevoted_rounds
    assert node0.fsm.votes_cast[-1].block_hash == h1


def test_network_logs_record_nonzero_height_for_consensus_traffic() -> None:
    _, nodes, sim, logger, _, users = build_integrated_cluster(validator_count=4)
    tx = sign_tx("Alice", users["Alice"], "Alice/h", "1", 1)
    nodes["node0"].submit_transaction(tx)
    for n in nodes.values():
        n.start(0.0)
    sim.run_until(1.5)
    send_events = [r for r in logger.records if r.event_type == "SEND"]
    assert any(r.height >= 1 and "HEADER" in r.detail for r in send_events)
    assert any(r.height >= 1 and "VOTE" in r.detail for r in send_events)
