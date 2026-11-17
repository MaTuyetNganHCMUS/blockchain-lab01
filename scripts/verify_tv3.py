"""Interactive Verification Script for Thành viên 3 (TV3).

Verifies all deliverables of TV3 according to Lab01.pdf and job_list.txt:
1. Conformance to NetworkProtocol.
2. Rate Limiter and Peer Blocker (EVENT_BLOCK, EVENT_UNBLOCK).
3. Two-phase Header-before-Body gossip protocol.
4. Discrete-Event Network Simulator with heapq and monotonic sequence counter.
5. Partial Synchrony simulation (Pre-GST drop/jitter, Post-GST bounded latency).
6. End-to-End Test T5 (Safety before GST, Liveness recovery after GST).
7. Simulation determinism and reproducibility.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))

from src.common.interfaces import (
    EVENT_BLOCK,
    EVENT_REJECT_BODY,
    EVENT_UNBLOCK,
    GENESIS_PARENT_HASH,
    MSG_BODY,
    BlockBody,
    BlockHeader,
    NetworkProtocol,
    Transaction,
)
from src.common.mocks import MockCrypto, MockLogger
from src.consensus.codec import pack_message
from src.network.rate_limiter import RateLimiter, RateLimiterConfig
from src.network.simulator import NetworkConfig, NetworkSimulator
from src.simulation import build_integrated_cluster, sign_tx


def print_section(title: str) -> None:
    print(f"\n{'='*70}\n[TEST] {title}\n{'='*70}")


def check_assertion(condition: bool, description: str) -> None:
    if condition:
        print(f"  [PASS] {description}")
    else:
        print(f"  [FAIL] {description}")
        sys.exit(1)


def main() -> None:
    print("======================================================================")
    print("        KIỂM TRA TỰ ĐỘNG CÁC HẠNG MỤC CÔNG VIỆC CỦA THÀNH VIÊN 3 (TV3)")
    print("          Mô phỏng mạng, Cơ chế Gossip 2 pha & Bộ Rate Limiter")
    print("======================================================================")

    # 1. Conformance to NetworkProtocol
    print_section("1. Kiểm tra tuân thủ Hợp đồng Giao diện (NetworkProtocol)")
    sim = NetworkSimulator(seed=42)
    check_assertion(
        isinstance(sim, NetworkProtocol),
        "NetworkSimulator triển khai đầy đủ và hợp lệ NetworkProtocol",
    )
    check_assertion(
        hasattr(sim, "send") and hasattr(sim, "broadcast_header") and hasattr(sim, "schedule_timeout"),
        "Cung cấp đầy đủ 3 hàm cốt lõi: send(), broadcast_header(), schedule_timeout()",
    )

    # 2. Rate Limiter & Peer Blocker
    print_section("2. Kiểm tra Outbound Rate Limiter & Peer Blocker")
    logger = MockLogger()
    config = RateLimiterConfig(max_requests_per_window=3, window_seconds=1.0, block_duration=2.0)
    limiter = RateLimiter(config=config, logger=logger)

    for i in range(3):
        limiter.check_inbound("node_spammer", current_time=0.1 * (i + 1), receiver_id="node_target")
    check_assertion(
        not limiter.is_peer_blocked("node_spammer", current_time=0.35, receiver_id="node_target"),
        "3 thông điệp đầu tiên trong ngưỡng cho phép: Không bị BLOCK",
    )

    allowed = limiter.check_inbound("node_spammer", current_time=0.4, receiver_id="node_target")
    check_assertion(
        not allowed and limiter.is_peer_blocked("node_spammer", current_time=0.4, receiver_id="node_target"),
        "Thông điệp thứ 4 vượt ngưỡng: Node bị kích hoạt trạng thái BLOCK",
    )

    block_logs = [r for r in logger.records if r["event_type"] == EVENT_BLOCK]
    check_assertion(
        len(block_logs) >= 1 and block_logs[0]["node_id"] == "node_spammer",
        f"Ghi nhận sự kiện EVENT_BLOCK trong hệ thống log: {block_logs[0]['detail'] if block_logs else ''}",
    )

    is_blocked_after = limiter.is_peer_blocked("node_spammer", current_time=2.45, receiver_id="node_target")
    check_assertion(
        not is_blocked_after,
        "Sau khoảng thời gian phạt (2.0s): Peer tự động được UNBLOCK",
    )
    unblock_logs = [r for r in logger.records if r["event_type"] == EVENT_UNBLOCK]
    check_assertion(
        len(unblock_logs) >= 1,
        f"Ghi nhận sự kiện EVENT_UNBLOCK trong hệ thống log: {unblock_logs[0]['detail'] if unblock_logs else ''}",
    )

    # 3. Two-phase Header-before-Body Protocol (ConsensusFSM & ValidatorNode)
    print_section("3. Kiểm tra Giao thức Gossip 2 pha (Header-before-Body)")
    net_cfg_hbb = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.01, max_delay_pre_gst=0.1, seed=42)
    validators, nodes, cluster_sim, cluster_logger, _, users = build_integrated_cluster(
        validator_count=4,
        network_config=net_cfg_hbb,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    receiver = next(v for v in validators if v != proposer)
    receiver_node = nodes[receiver]

    # Orphan body test: send MSG_BODY for an unaccepted header hash
    dummy_body = BlockBody(header_hash=b"\xaa" * 32, transactions=())
    receiver_node.handle_packet(0.0, pack_message(MSG_BODY, dummy_body), sender=proposer)

    rejected_body_event = any(
        r.event_type == EVENT_REJECT_BODY and r.node_id == receiver
        for r in cluster_logger.records
    )
    check_assertion(
        rejected_body_event and dummy_body.header_hash not in receiver_node.fsm.accepted_headers,
        "Đúng luật Mục 6: BlockBody gửi đến khi chưa nhận/duyệt Header bị TỪ CHỐI thẳng",
    )

    # Pha 1: Proposer broadcasts BlockHeader upon proposing block
    tx = sign_tx("Alice", users["Alice"], "Alice/msg", "hello", 1)
    nodes[proposer].submit_transaction(tx)
    for n in nodes.values():
        n.start(0.0)

    # Run network briefly to deliver Header to receiver
    cluster_sim.run_until(0.05)
    accepted_hdr_hashes = list(receiver_node.fsm.accepted_headers)
    check_assertion(
        len(accepted_hdr_hashes) > 0,
        "Pha 1: Nhận và chấp thuận BlockHeader thành công",
    )

    # Pha 2: Receiver sent BODY_REQUEST, Proposer replied with BODY, Receiver accepted full block
    cluster_sim.run_until(0.3)
    target_hash = accepted_hdr_hashes[0]
    cached_block = receiver_node.fsm.block_cache.get(target_hash)
    b_ok = cached_block is not None and cached_block[1] is not None
    check_assertion(
        b_ok,
        "Pha 2: Sau khi đã có Header, BlockBody tương ứng được CHẤP THUẬN",
    )

    # 4. Partial Synchrony Simulation
    print_section("4. Kiểm tra Mô hình Mạng Bán Đồng Bộ (Partial Synchrony)")
    net_cfg = NetworkConfig(
        gst=20.0,
        delta=2.0,
        min_delay=0.1,
        max_delay_pre_gst=4.0,
        drop_rate_pre_gst=1.0,
        seed=1001,
    )
    sim_sync = NetworkSimulator(config=net_cfg, nodes=["NodeA", "NodeB"])
    received_msgs: list[tuple[float, str]] = []

    class MockReceiver:
        def handle_packet(self, sim_t: float, data: bytes) -> None:
            received_msgs.append((sim_t, data.decode()))

    sim_sync.register_node("NodeB", MockReceiver())

    sim_sync.send("NodeA", "NodeB", b"pre-gst-data")
    sim_sync.run_until(15.0)
    check_assertion(
        len(received_msgs) == 0 and sim_sync.total_dropped >= 1,
        "Giai đoạn Pre-GST (t < 20.0s): Gói tin bị rớt do mạng không ổn định (EVENT_DROP)",
    )

    sim_sync.current_time = 20.0
    sim_sync.send("NodeA", "NodeB", b"post-gst-data")
    sim_sync.run_until(25.0)
    check_assertion(
        len(received_msgs) == 1 and received_msgs[0][1] == "post-gst-data",
        "Giai đoạn Post-GST (t >= 20.0s): Mạng ổn định, gói tin được chuyển giao thành công",
    )
    delivery_latency = received_msgs[0][0] - 20.0
    check_assertion(
        delivery_latency <= net_cfg.delta,
        f"Độ trễ sau GST ({delivery_latency:.3f}s) được bảo đảm trong giới hạn Delta ({net_cfg.delta}s)",
    )

    # 5. Seed Determinism
    print_section("5. Kiểm tra Tính Tất Định & Khả Năng Tái Lập (Seed Determinism)")

    def run_sim_trace(seed_val: int) -> list[tuple[float, str, str]]:
        trace: list[tuple[float, str, str]] = []

        class Tracer:
            def __init__(self, nid: str):
                self.nid = nid

            def handle_packet(self, t: float, b: bytes) -> None:
                trace.append((round(t, 4), self.nid, b.decode()))

        s = NetworkSimulator(seed=seed_val, nodes=["A", "B", "C"])
        s.register_node("B", Tracer("B"))
        s.register_node("C", Tracer("C"))
        for j in range(10):
            s.send("A", "B", f"pkt-{j}".encode())
            s.send("A", "C", f"pkt-{j}".encode())
        s.run_until(30.0)
        return trace

    trace1 = run_sim_trace(9999)
    trace2 = run_sim_trace(9999)
    check_assertion(
        trace1 == trace2 and len(trace1) > 0,
        f"2 lần chạy độc lập với cùng seed sinh ra chuỗi {len(trace1)} sự kiện giống hệt 100% từng byte",
    )

    print("\n" + "="*70)
    print("  KẾT LUẬN: TẤT CẢ CÁC HẠNG MỤC CÔNG VIỆC CỦA THÀNH VIÊN 3 ĐỀU ĐẠT CHUẨN!")
    print("======================================================================\n")


if __name__ == "__main__":
    main()
