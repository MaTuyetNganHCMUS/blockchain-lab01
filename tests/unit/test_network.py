"""Unit tests for Network Simulator, Rate Limiter, and Gossip Protocol (TV3).

Tests:
1. Rate Limiter: Spam detection, peer BLOCK, and automatic UNBLOCK after penalty window.
2. Gossip Protocol: Header-before-Body rule, rejecting bodies for unaccepted headers.
3. Network Simulator: Deterministic discrete-event priority queue, partial synchrony, timeouts.
"""

from __future__ import annotations

import pytest

from src.common.interfaces import (
    DOMAIN_HEADER,
    EVENT_BLOCK,
    EVENT_DROP,
    EVENT_REJECT_BODY,
    EVENT_REJECT_HEADER,
    EVENT_UNBLOCK,
    GENESIS_PARENT_HASH,
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    NIL_BLOCK_HASH,
    PHASE_PREVOTE,
    REASON_INVALID_BLOCK,
    REASON_INVALID_SIGNATURE,
    REASON_UNACCEPTED_HEADER,
    REASON_WRONG_PROPOSER,
    BlockBody,
    BlockHeader,
    NetworkProtocol,
    Transaction,
)
from src.common.mocks import MockCrypto, MockLogger
from src.consensus.codec import pack_message
from src.crypto import sign
from src.execution import compute_header_hash, compute_tx_hash, compute_tx_root
from src.execution.commitment import header_signing_payload
from src.network.rate_limiter import RateLimiter, RateLimiterConfig
from src.network.simulator import NetworkConfig, NetworkSimulator
from src.simulation import build_integrated_cluster, sign_tx


# ==============================================================================
# 1. RATE LIMITER & PEER BLOCKER TESTS
# ==============================================================================


def test_rate_limiter_normal_traffic() -> None:
    """Normal traffic within threshold is permitted."""
    config = RateLimiterConfig(max_requests_per_window=5, window_seconds=1.0)
    limiter = RateLimiter(config=config)

    for i in range(5):
        assert limiter.check_inbound("node1", current_time=0.1 * i) is True
    assert limiter.is_peer_blocked("node1", current_time=0.5) is False


def test_rate_limiter_peer_block_and_unblock() -> None:
    """Spamming node is blocked and automatically unblocked after delta_t."""
    logger = MockLogger()
    config = RateLimiterConfig(
        max_requests_per_window=3,
        window_seconds=1.0,
        block_duration=2.0,
    )
    limiter = RateLimiter(config=config, logger=logger)

    # Send 3 messages within 0.3s -> OK
    assert limiter.check_inbound("spammer", current_time=0.1) is True
    assert limiter.check_inbound("spammer", current_time=0.2) is True
    assert limiter.check_inbound("spammer", current_time=0.3) is True

    # 4th message in same window -> exceeds threshold (3), triggers BLOCK
    assert limiter.check_inbound("spammer", current_time=0.4) is False
    assert limiter.is_peer_blocked("spammer", current_time=0.4) is True

    # Check logger recorded EVENT_BLOCK
    block_logs = [r for r in logger.records if r["event_type"] == EVENT_BLOCK]
    assert len(block_logs) == 1
    assert block_logs[0]["node_id"] == "spammer"

    # Still blocked at t = 1.5s
    assert limiter.check_inbound("spammer", current_time=1.5) is False
    assert limiter.is_peer_blocked("spammer", current_time=1.5) is True

    # At t = 2.4s (0.4 + 2.0 = 2.4s), block penalty expires -> UNBLOCK
    assert limiter.is_peer_blocked("spammer", current_time=2.4) is False
    unblock_logs = [r for r in logger.records if r["event_type"] == EVENT_UNBLOCK]
    assert len(unblock_logs) == 1
    assert unblock_logs[0]["node_id"] == "spammer"

    # New message at t = 2.5s is accepted
    assert limiter.check_inbound("spammer", current_time=2.5) is True


def test_outbound_rate_limiting() -> None:
    """Outbound limiter prevents sending beyond configured capacity."""
    config = RateLimiterConfig(max_outbound_per_window=2, outbound_window_seconds=1.0)
    limiter = RateLimiter(config=config)

    assert limiter.check_outbound("node0", current_time=0.0) is True
    assert limiter.check_outbound("node0", current_time=0.5) is True
    # Exceeds quota of 2
    assert limiter.check_outbound("node0", current_time=0.8) is False
    # Allowed in next window
    assert limiter.check_outbound("node0", current_time=1.5) is True


# ==============================================================================
# 2. HEADER-BEFORE-BODY & TRANSACTION GOSSIP TESTS (ConsensusFSM & ValidatorNode)
# ==============================================================================


def test_header_before_body_case1_reject_body_without_accepted_header() -> None:
    """CASE 1: Validator receiving MSG_BODY before accepting matching BlockHeader rejects it."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    tx = sign_tx("Alice", users["Alice"], "Alice/msg", "val1", 1)
    unaccepted_hash = b"\x11" * 32
    body = BlockBody(header_hash=unaccepted_hash, transactions=(tx,))

    # Deliver MSG_BODY without any header having been received or accepted
    orphan_packet = pack_message(MSG_BODY, body)
    receiver_node.handle_packet(0.0, orphan_packet, sender=proposer)

    # 1. Not accepted as valid block, not in accepted_headers or block_cache
    assert unaccepted_hash not in receiver_node.fsm.accepted_headers
    assert unaccepted_hash not in receiver_node.fsm.block_cache
    # 2. Rejection event logged
    rejects = [
        r for r in logger.records
        if r.event_type == EVENT_REJECT_BODY and r.node_id == receiver
    ]
    assert len(rejects) == 1
    assert REASON_UNACCEPTED_HEADER in rejects[0].detail
    # 3. No valid PREVOTE cast for this block
    prevotes = [v for v in receiver_node.fsm.votes_cast if v.phase == PHASE_PREVOTE and v.block_hash == unaccepted_hash]
    assert len(prevotes) == 0
    # 4. Not finalized
    assert not receiver_node.ledger.is_finalized(1, unaccepted_hash)


def test_header_before_body_case2_valid_two_phase_flow() -> None:
    """CASE 2: Valid Header-before-Body flow with strict transmission ordering verification."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, max_delay_pre_gst=0.05, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    tx = sign_tx("Alice", users["Alice"], "Alice/data", "ok", 1)
    nodes[proposer].submit_transaction(tx)

    for n in nodes.values():
        n.start(0.0)

    # Run simulation to allow proposal, body pull, and vote progression
    sim.run_until(1.0)

    # Verify message sequence order in logger:
    # 1. Proposer broadcasts MSG_HEADER -> receiver receives MSG_HEADER
    # 2. Receiver validates header and sends MSG_BODY_REQUEST to proposer
    # 3. Proposer responds with MSG_BODY to receiver
    # 4. Receiver verifies and accepts body, advancing consensus
    header_send_t: float | None = None
    body_req_send_t: float | None = None
    body_send_t: float | None = None

    for rec in logger.records:
        if rec.event_type != "SEND":
            continue
        if rec.node_id == proposer and f"Send {MSG_HEADER} to {receiver}" in rec.detail:
            if header_send_t is None:
                header_send_t = float(rec.sim_time)
        elif rec.node_id == receiver and f"Send {MSG_BODY_REQUEST} to {proposer}" in rec.detail:
            if body_req_send_t is None:
                body_req_send_t = float(rec.sim_time)
        elif rec.node_id == proposer and f"Send {MSG_BODY} to {receiver}" in rec.detail:
            if body_send_t is None:
                body_send_t = float(rec.sim_time)

    assert header_send_t is not None, "Proposer must broadcast MSG_HEADER"
    assert body_req_send_t is not None, "Receiver must send MSG_BODY_REQUEST after header"
    assert body_send_t is not None, "Proposer must reply with MSG_BODY"
    assert header_send_t <= body_req_send_t <= body_send_t, (
        f"Strict ordering violated: header={header_send_t}, req={body_req_send_t}, body={body_send_t}"
    )

    # Receiver has full block in cache and committed state
    accepted_hashes = list(receiver_node.fsm.accepted_headers)
    assert len(accepted_hashes) >= 1
    hdr_hash = accepted_hashes[0]
    cached = receiver_node.fsm.block_cache.get(hdr_hash)
    assert cached is not None and cached[0] is not None and cached[1] is not None
    assert receiver_node.ledger.height >= 1


def test_header_before_body_case3_invalid_header_rejected() -> None:
    """CASE 3: Header with wrong proposer or invalid signature is rejected; no BODY_REQUEST sent."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    # Header with invalid proposer
    bad_proposer_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"\x01" * 32,
        tx_root=b"\x02" * 32,
        proposer=receiver,  # receiver is NOT the expected proposer
        sig=b"\x00" * 64,
    )
    receiver_node.handle_packet(0.0, pack_message(MSG_HEADER, bad_proposer_header), sender=receiver)

    # Verify header rejected
    assert len(receiver_node.fsm.accepted_headers) == 0
    reject_proposer = [
        r for r in logger.records
        if r.event_type == EVENT_REJECT_HEADER and r.node_id == receiver and REASON_WRONG_PROPOSER in r.detail
    ]
    assert len(reject_proposer) == 1

    # Header with invalid signature from the correct proposer
    bad_sig_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"\x01" * 32,
        tx_root=b"\x02" * 32,
        proposer=proposer,
        sig=b"\xff" * 64,  # corrupt signature
    )
    receiver_node.handle_packet(0.01, pack_message(MSG_HEADER, bad_sig_header), sender=proposer)

    assert len(receiver_node.fsm.accepted_headers) == 0
    reject_sig = [
        r for r in logger.records
        if r.event_type == EVENT_REJECT_HEADER and r.node_id == receiver and REASON_INVALID_SIGNATURE in r.detail
    ]
    assert len(reject_sig) == 1

    # Crucial: No MSG_BODY_REQUEST must have been sent for either rejected header
    body_reqs = [
        r for r in logger.records
        if r.event_type == "SEND" and r.node_id == receiver and MSG_BODY_REQUEST in r.detail
    ]
    assert len(body_reqs) == 0


def test_header_before_body_case4_mismatched_body_rejected() -> None:
    """CASE 4: Body not matching accepted header (tx_root/state_hash mismatch) is rejected."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    # Construct a valid signed header
    tx_good = sign_tx("Alice", users["Alice"], "Alice/key", "val", 1)
    ok, exp_state = receiver_node.state.validate_and_compute_hash([tx_good])
    assert ok
    exp_root = compute_tx_root([compute_tx_hash(tx_good)])
    parent = receiver_node.fsm._expected_parent_hash()

    raw_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent,
        state_hash=exp_state,
        tx_root=exp_root,
        proposer=proposer,
    )
    sig = sign(nodes[proposer].fsm.priv_key, f"{DOMAIN_HEADER}:lab01", header_signing_payload(raw_header))
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent,
        state_hash=exp_state,
        tx_root=exp_root,
        proposer=proposer,
        sig=sig,
    )
    header_hash = compute_header_hash(signed_header)

    # Deliver valid header
    receiver_node.handle_packet(0.0, pack_message(MSG_HEADER, signed_header), sender=proposer)
    assert header_hash in receiver_node.fsm.accepted_headers

    # Deliver mismatched body (wrong transactions causing tx_root & state_hash mismatch)
    tx_bad = sign_tx("Bob", users["Bob"], "Bob/other", "val2", 1)
    mismatched_body = BlockBody(header_hash=header_hash, transactions=(tx_bad,))
    receiver_node.handle_packet(0.02, pack_message(MSG_BODY, mismatched_body), sender=proposer)

    # Verify body rejected
    rejects = [
        r for r in logger.records
        if r.event_type == EVENT_REJECT_BODY and r.node_id == receiver and REASON_INVALID_BLOCK in r.detail
    ]
    assert len(rejects) == 1
    # Node must vote NIL and never vote for or finalize this invalid block
    assert any(v.phase == PHASE_PREVOTE and v.block_hash == NIL_BLOCK_HASH for v in receiver_node.fsm.votes_cast)
    assert not any(v.phase == PHASE_PREVOTE and v.block_hash == header_hash for v in receiver_node.fsm.votes_cast)
    assert not receiver_node.ledger.is_finalized(1, header_hash)


def test_header_before_body_case5_body_retry_and_recovery() -> None:
    """CASE 5: Lost/delayed body triggers BODY_RETRY timeout; consensus recovers when body arrives."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    tx = sign_tx("Alice", users["Alice"], "Alice/retry", "recovered", 1)
    ok, exp_state = receiver_node.state.validate_and_compute_hash([tx])
    assert ok
    exp_root = compute_tx_root([compute_tx_hash(tx)])
    parent = receiver_node.fsm._expected_parent_hash()

    raw_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent,
        state_hash=exp_state,
        tx_root=exp_root,
        proposer=proposer,
    )
    sig = sign(nodes[proposer].fsm.priv_key, f"{DOMAIN_HEADER}:lab01", header_signing_payload(raw_header))
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent,
        state_hash=exp_state,
        tx_root=exp_root,
        proposer=proposer,
        sig=sig,
    )
    header_hash = compute_header_hash(signed_header)
    matching_body = BlockBody(header_hash=header_hash, transactions=(tx,))

    # Deliver header at t = 0.0
    receiver_node.handle_packet(0.0, pack_message(MSG_HEADER, signed_header), sender=proposer)
    assert header_hash in receiver_node.fsm.accepted_headers

    # Initial BODY_REQUEST was sent, and retry timeout was scheduled
    assert receiver_node.fsm.requested_bodies.get(header_hash, 0) == 1
    assert receiver_node.fsm.block_cache.get(header_hash) is None

    # Advance time: BODY_RETRY timeout triggers
    receiver_node.handle_timeout(0.5, 1, 0, "BODY_RETRY")
    assert receiver_node.fsm.requested_bodies.get(header_hash, 0) == 2

    # Now deliver the matching body
    receiver_node.handle_packet(0.55, pack_message(MSG_BODY, match_body if "match_body" in locals() else matching_body), sender=proposer)

    # Block body is accepted into cache, and consensus can continue
    cached = receiver_node.fsm.block_cache.get(header_hash)
    assert cached is not None and cached[1] == matching_body
    assert any(v.phase == PHASE_PREVOTE and v.block_hash == header_hash for v in receiver_node.fsm.votes_cast)


def test_header_before_body_case6_non_proposer_tx_gossip() -> None:
    """CASE 6: Transaction submitted to non-proposer is admitted, gossiped to proposer, and deduplicated."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    non_proposer = next(v for v in validators if v != proposer)

    tx = sign_tx("Alice", users["Alice"], "Alice/gossip_test", "passed", 1)

    # Proposer mempool starts empty
    assert len(nodes[proposer].fsm.mempool) == 0

    # Submit to non-proposer
    admit_res = nodes[non_proposer].admit_transaction(tx, sim_time=0.0)
    assert admit_res.accepted is True
    assert admit_res.tx_hash in nodes[non_proposer].fsm.mempool

    # Run network so bounded gossip delivers TX to peers
    sim.run_until(0.1)

    # Proposer received gossiped TX in its mempool
    assert admit_res.tx_hash in nodes[proposer].fsm.mempool

    # Duplicate submission to non-proposer is rejected
    admit_dup = nodes[non_proposer].admit_transaction(tx, sim_time=0.2)
    assert admit_dup.accepted is False


# ==============================================================================
# 3. NETWORK SIMULATOR & DISCRETE-EVENT TESTS
# ==============================================================================


def test_network_simulator_conforms_to_protocol() -> None:
    """NetworkSimulator satisfies NetworkProtocol runtime check."""
    sim = NetworkSimulator()
    assert isinstance(sim, NetworkProtocol)


def test_network_simulator_determinism() -> None:
    """Two identical simulators with same seed yield byte-identical simulation events."""
    packets_log1: list[tuple[float, str, str]] = []
    packets_log2: list[tuple[float, str, str]] = []

    def make_handler(sink: list[tuple[float, str, str]], nid: str):
        class Handler:
            def handle_packet(self, t: float, data: bytes) -> None:
                sink.append((round(t, 4), nid, data.decode()))

        return Handler()

    # Run 1
    sim1 = NetworkSimulator(seed=12345, nodes=["A", "B", "C"])
    sim1.register_node("B", make_handler(packets_log1, "B"))
    sim1.register_node("C", make_handler(packets_log1, "C"))
    sim1.send("A", "B", b"hello-1")
    sim1.send("A", "C", b"hello-2")
    sim1.broadcast_header("A", b"header-bytes")
    sim1.run_until(10.0)

    # Run 2
    sim2 = NetworkSimulator(seed=12345, nodes=["A", "B", "C"])
    sim2.register_node("B", make_handler(packets_log2, "B"))
    sim2.register_node("C", make_handler(packets_log2, "C"))
    sim2.send("A", "B", b"hello-1")
    sim2.send("A", "C", b"hello-2")
    sim2.broadcast_header("A", b"header-bytes")
    sim2.run_until(10.0)

    assert packets_log1 == packets_log2
    assert len(packets_log1) > 0


def test_network_simulator_timeouts() -> None:
    """Timeouts fire at designated future times and invoke callbacks."""
    sim = NetworkSimulator()
    timeout_events: list[tuple[float, int, int, str]] = []

    class MockNode:
        def handle_timeout(self, sim_time: float, h: int, r: int, step: str) -> None:
            timeout_events.append((sim_time, h, r, step))

    sim.register_node("nodeA", MockNode())
    sim.schedule_timeout("nodeA", delay=1.5, callback_tag="1:0:PROPOSE")
    sim.schedule_timeout("nodeA", delay=3.0, callback_tag="1:0:PREVOTE")

    # Run until t = 2.0 -> only first timeout fired
    sim.run_until(2.0)
    assert len(timeout_events) == 1
    assert timeout_events[0] == (1.5, 1, 0, "PROPOSE")

    # Run until t = 4.0 -> second timeout fired
    sim.run_until(4.0)
    assert len(timeout_events) == 2
    assert timeout_events[1] == (3.0, 1, 0, "PREVOTE")


def test_partial_synchrony_pre_and_post_gst() -> None:
    """Pre-GST experiences drop rate; Post-GST delivers bounded within Delta without drop."""
    logger = MockLogger()
    config = NetworkConfig(
        gst=50.0,
        delta=2.0,
        min_delay=0.1,
        max_delay_pre_gst=4.0,
        drop_rate_pre_gst=1.0,  # 100% drop pre-GST to strictly test boundary
        seed=999,
    )
    sim = NetworkSimulator(config=config, nodes=["A", "B"], logger=logger)
    received: list[tuple[float, str]] = []

    class Receiver:
        def handle_packet(self, t: float, data: bytes) -> None:
            received.append((t, data.decode()))

    sim.register_node("B", Receiver())

    # Pre-GST send at t = 0 -> 100% dropped
    sim.send("A", "B", b"pre-gst-msg")
    sim.run_until(10.0)
    assert len(received) == 0
    drop_logs = [r for r in logger.records if r["event_type"] == EVENT_DROP]
    assert len(drop_logs) == 1

    # Advance clock to post-GST (t = 50.0)
    sim.current_time = 50.0
    sim.send("A", "B", b"post-gst-msg")
    sim.run_until(60.0)

    # Post-GST message must be delivered within Delta (<= 50.0 + 2.0 = 52.0)
    assert len(received) == 1
    arrival_time, msg = received[0]
    assert msg == "post-gst-msg"
    assert arrival_time <= 50.0 + config.delta
