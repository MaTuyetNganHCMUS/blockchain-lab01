"""Unit tests for Network Simulator, Rate Limiter, and Gossip Protocol (TV3).

Tests:
1. Rate Limiter: Spam detection, peer BLOCK, and automatic UNBLOCK after penalty window.
2. Gossip Protocol: Header-before-Body rule, rejecting bodies for unaccepted headers.
3. Network Simulator: Deterministic discrete-event priority queue, partial synchrony, timeouts.
"""

from __future__ import annotations

import pytest

from src.common.interfaces import (
    EVENT_BLOCK,
    EVENT_DROP,
    EVENT_UNBLOCK,
    GENESIS_PARENT_HASH,
    BlockBody,
    BlockHeader,
    NetworkProtocol,
    Transaction,
)
from src.common.mocks import MockCrypto, MockLogger
from src.network.gossip import GossipManager
from src.network.rate_limiter import RateLimiter, RateLimiterConfig
from src.network.simulator import NetworkConfig, NetworkSimulator


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
# 2. GOSSIP PROTOCOL (HEADER-BEFORE-BODY) TESTS
# ==============================================================================


def test_gossip_reject_body_without_accepted_header() -> None:
    """Sending BlockBody when receiver has not accepted BlockHeader is rejected."""
    receiver_gossip = GossipManager(node_id="receiver")

    tx = Transaction(sender="Alice", key="Alice/msg", value="hello", nonce=1)
    body = BlockBody(header_hash=b"unaccepted_header_hash", transactions=[tx])

    # Receiver has NOT accepted header b"unaccepted_header_hash"
    accepted = receiver_gossip.handle_body_packet(sender="proposer", body=body)
    assert accepted is False
    assert receiver_gossip.has_full_block(b"unaccepted_header_hash") is False


def test_gossip_normal_two_phase_flow() -> None:
    """Normal two-phase gossip: Header broadcast -> Accepted -> Body accepted."""
    crypto = MockCrypto()
    sender_gossip = GossipManager(node_id="sender", crypto=crypto)
    receiver_gossip = GossipManager(node_id="receiver", crypto=crypto)

    tx = Transaction(sender="Alice", key="Alice/data", value="v1", nonce=1)
    header = BlockHeader(
        height=1,
        round=0,
        parent_hash=GENESIS_PARENT_HASH,
        state_hash=b"state1",
        tx_root=b"txroot1",
        proposer="sender",
    )
    header_hash = sender_gossip._compute_header_hash(header)
    body = BlockBody(header_hash=header_hash, transactions=[tx])

    # 1. Sender prepares proposal with header and body
    sender_gossip.stored_bodies[header_hash] = body

    # 2. Receiver gets header
    assert receiver_gossip.handle_header_packet("sender", header) is True
    assert header_hash in receiver_gossip.accepted_headers

    # 3. Receiver now receives body -> accepted!
    assert receiver_gossip.handle_body_packet("sender", body) is True
    assert receiver_gossip.has_full_block(header_hash) is True

    retrieved = receiver_gossip.get_block(header_hash)
    assert retrieved is not None
    assert retrieved[0] == header
    assert retrieved[1] == body


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
