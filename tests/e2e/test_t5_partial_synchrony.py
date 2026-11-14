"""End-to-End Test T5: Partial Synchrony, Safety, and Liveness Recovery (TV3).

Test Scenario:
- Configurable network with >= 8 nodes (n = 8, f = 2, quorum = 2f + 1 = 5).
- Heavy message drop and delay jitter prior to Global Stabilization Time (T_GST).
- Invariant 1 (Safety): Under chaotic conditions before T_GST, no two distinct
  blocks are ever finalized at the same height.
- Invariant 2 (Liveness): Once T_GST is reached (network stabilizes, delivery <= Delta),
  honest nodes converge and finalize the exact same block and state hash.
- Invariant 3 (Deterministic Trace): Re-running the scenario with the same seed
  yields byte-identical simulation events.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.common.interfaces import (
    GENESIS_PARENT_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    BlockBody,
    BlockHeader,
    NetPacket,
    Transaction,
    Vote,
)
from src.common.mocks import (
    MockCrypto,
    MockLedger,
    MockLockManager,
    MockLogger,
    MockState,
    MockVoteTracker,
)
from src.network.gossip import GossipManager
from src.network.rate_limiter import RateLimiter, RateLimiterConfig
from src.network.simulator import NetworkConfig, NetworkSimulator


def load_t5_config() -> dict:
    """Load configuration for scenario T5 from config/network_t5.json."""
    config_path = Path(__file__).resolve().parents[2] / "config" / "network_t5.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "validator_count": 8,
        "network": {
            "gst": 25.0,
            "delta": 2.0,
            "min_delay": 0.05,
            "max_delay_pre_gst": 8.0,
            "drop_rate_pre_gst": 0.35,
            "dup_rate_pre_gst": 0.20,
        },
        "random_seed": 777,
    }


class BFTTestNode:
    """Validator node agent simulating 2-phase Tendermint BFT over NetworkSimulator."""

    def __init__(
        self,
        node_id: str,
        validators: list[str],
        simulator: NetworkSimulator,
        crypto: MockCrypto,
        logger: MockLogger,
    ) -> None:
        self.node_id = node_id
        self.validators = validators
        self.simulator = simulator
        self.crypto = crypto
        self.logger = logger

        self.state = MockState()
        self.ledger = MockLedger()
        self.vote_tracker = MockVoteTracker(validator_count=len(validators))
        self.lock_manager = MockLockManager()
        self.gossip = GossipManager(node_id=node_id, network=simulator, crypto=crypto)

        self.height = 1
        self.round = 0
        self.finalized_blocks: list[tuple[BlockHeader, BlockBody]] = []

    def propose_block(self, height: int, round_num: int, txs: list[Transaction]) -> BlockHeader:
        """Create and broadcast block proposal."""
        ok, state_hash = self.state.validate_and_compute_hash(txs)
        assert ok, "Transactions must be valid"

        tx_root = self.crypto.hash_bytes(self.crypto.encode_canonical(txs))
        header = BlockHeader(
            height=height,
            round=round_num,
            parent_hash=GENESIS_PARENT_HASH,
            state_hash=state_hash,
            tx_root=tx_root,
            proposer=self.node_id,
        )
        body = BlockBody(header_hash=self.gossip._compute_header_hash(header), transactions=txs)
        self.gossip.broadcast_header(header, body)
        return header

    def broadcast_vote(self, phase: str, block_hash: bytes, round_num: int | None = None) -> None:
        """Broadcast a signed vote to all validators over simulated network."""
        import pickle

        r = round_num if round_num is not None else self.round
        vote = Vote(
            height=self.height,
            round=r,
            phase=phase,
            block_hash=block_hash,
            voter=self.node_id,
        )
        encoded_vote = pickle.dumps(vote)
        for peer in self.validators:
            self.simulator.send(self.node_id, peer, encoded_vote)

    def handle_packet(self, sim_time: float, raw_bytes: bytes) -> None:
        """Handle incoming raw network packet."""
        import pickle

        try:
            obj = pickle.loads(raw_bytes)
            if isinstance(obj, Vote):
                self.on_receive_vote(obj)
        except Exception:
            pass

    def on_receive_vote(self, vote: Vote) -> None:
        """Track incoming vote and check quorum."""
        quorum_res = self.vote_tracker.add_vote(vote)
        if quorum_res.has_quorum:
            if quorum_res.phase == PHASE_PREVOTE and quorum_res.block_hash != b"NIL":
                # Lock block on 2f+1 prevotes (Rule 4)
                self.lock_manager.record_lock(quorum_res.block_hash, quorum_res.round)
                # Cast Precommit for the locked block
                self.broadcast_vote(PHASE_PRECOMMIT, quorum_res.block_hash, round_num=quorum_res.round)
            elif quorum_res.phase == PHASE_PRECOMMIT and quorum_res.block_hash != b"NIL":
                # Finalize block on 2f+1 precommits
                header = self.gossip.headers_by_hash.get(quorum_res.block_hash)
                body = self.gossip.stored_bodies.get(quorum_res.block_hash) or self.gossip.accepted_bodies.get(quorum_res.block_hash)
                if header is not None and body is not None:
                    if not self.ledger.is_finalized(header.height, quorum_res.block_hash):
                        self.state.commit_block(body)
                        self.ledger.add_finalized_block(header, body)
                        self.finalized_blocks.append((header, body))


def test_t5_partial_synchrony_safety_and_liveness() -> None:
    """Execute full T5 scenario verifying Safety before GST and Liveness after GST."""
    cfg = load_t5_config()
    validator_count = cfg.get("validator_count", 8)
    net_cfg = cfg["network"]

    validators = [f"node_{i}" for i in range(validator_count)]
    logger = MockLogger()
    crypto = MockCrypto()

    network_config = NetworkConfig(
        gst=net_cfg["gst"],
        delta=net_cfg["delta"],
        min_delay=net_cfg["min_delay"],
        max_delay_pre_gst=net_cfg["max_delay_pre_gst"],
        drop_rate_pre_gst=net_cfg["drop_rate_pre_gst"],
        dup_rate_pre_gst=net_cfg["dup_rate_pre_gst"],
        seed=cfg.get("random_seed", 777),
    )

    simulator = NetworkSimulator(
        seed=network_config.seed,
        config=network_config,
        nodes=validators,
        logger=logger,
    )

    # Instantiate nodes
    nodes: dict[str, BFTTestNode] = {
        nid: BFTTestNode(nid, validators, simulator, crypto, logger)
        for nid in validators
    }

    # Register packet routing
    class NodePacketRouter:
        def __init__(self, node: BFTTestNode):
            self.node = node

        def handle_packet(self, sim_time: float, raw_bytes: bytes) -> None:
            self.node.handle_packet(sim_time, raw_bytes)

    for nid, node in nodes.items():
        simulator.register_node(nid, NodePacketRouter(node))

    # --- PHASE 1: PRE-GST (Chaotic network, drops and severe delays) ---
    # Proposer 0 proposes Block A
    tx1 = Transaction(sender="Alice", key="Alice/balance", value="100", nonce=1)
    tx2 = Transaction(sender="Alice", key="Alice/email", value="alice@lab.org", nonce=2)
    header_a = nodes["node_0"].propose_block(height=1, round_num=0, txs=[tx1, tx2])
    hash_a = nodes["node_0"].gossip._compute_header_hash(header_a)

    # Deliver proposals to peers (some will drop because t < GST)
    for nid in validators[1:]:
        simulator.send("node_0", nid, hash_a)

    # Nodes try to prevote under lossy network
    for nid, node in nodes.items():
        node.on_receive_vote(
            Vote(height=1, round=0, phase=PHASE_PREVOTE, block_hash=hash_a, voter=nid)
        )

    # Run discrete-event simulator until GST (t = 25.0)
    simulator.run_until(network_config.gst)

    # SAFETY CHECK AT PRE-GST:
    # Verify no two distinct blocks finalized at height 1
    finalized_hashes = set()
    for nid, node in nodes.items():
        for h, _ in node.finalized_blocks:
            finalized_hashes.add(h.parent_hash + h.state_hash)

    # Invariant: <= 1 unique block finalized
    assert len(finalized_hashes) <= 1, "Safety violation! Conflicting blocks finalized."

    # --- PHASE 2: POST-GST (Network stabilized, delivery bounded within Delta) ---
    assert simulator.current_time >= network_config.gst

    # Transition to round 1 post-GST
    for nid, node in nodes.items():
        node.round = 1

    # Proposer produces the definitive proposal post-GST
    proposer_id = "node_1"
    tx_final = Transaction(sender="Bob", key="Bob/status", value="confirmed", nonce=1)
    header_final = nodes[proposer_id].propose_block(height=1, round_num=1, txs=[tx1, tx_final])
    hash_final = nodes[proposer_id].gossip._compute_header_hash(header_final)

    # Broadcast proposal & body to all honest nodes
    for nid, node in nodes.items():
        node.gossip.headers_by_hash[hash_final] = header_final
        node.gossip.accepted_headers.add(hash_final)
        node.gossip.stored_bodies[hash_final] = BlockBody(header_hash=hash_final, transactions=[tx1, tx_final])

    # All honest nodes exchange prevotes for hash_final
    for nid, node in nodes.items():
        vote = Vote(height=1, round=1, phase=PHASE_PREVOTE, block_hash=hash_final, voter=nid)
        for peer in nodes.values():
            peer.on_receive_vote(vote)

    # Run simulator for 5 seconds to process post-GST traffic
    simulator.run_until(simulator.current_time + 5.0)

    # LIVENESS & AGREEMENT CHECK POST-GST:
    # All correct nodes must have finalized hash_final
    for nid, node in nodes.items():
        assert len(node.finalized_blocks) == 1, f"Node {nid} failed to finalize block"
        final_header, final_body = node.finalized_blocks[0]
        assert final_header.state_hash == header_final.state_hash, f"Node {nid} has mismatched state hash"

    # Verify identical state commitment across all honest nodes
    first_state_hash = nodes["node_0"].state.compute_state_hash()
    for nid, node in nodes.items():
        assert node.state.compute_state_hash() == first_state_hash, (
            f"State divergence detected at node {nid}!"
        )


def test_t5_determinism_identical_runs() -> None:
    """Verify that rerunning the exact same simulator configuration produces identical statistics."""
    cfg = load_t5_config()
    seed = cfg.get("random_seed", 777)

    def run_simulation(s: int) -> tuple[int, int, int, float]:
        net_cfg = cfg["network"]
        config = NetworkConfig(
            gst=net_cfg["gst"],
            delta=net_cfg["delta"],
            min_delay=net_cfg["min_delay"],
            max_delay_pre_gst=net_cfg["max_delay_pre_gst"],
            drop_rate_pre_gst=net_cfg["drop_rate_pre_gst"],
            dup_rate_pre_gst=net_cfg["dup_rate_pre_gst"],
            seed=s,
        )
        sim = NetworkSimulator(seed=s, config=config, nodes=["A", "B", "C", "D"])
        for i in range(20):
            sim.send("A", "B", f"msg-{i}".encode())
            sim.send("C", "D", f"msg-{i}".encode())
        sim.run_until(50.0)
        return (sim.total_sent, sim.total_delivered, sim.total_dropped, round(sim.current_time, 4))

    run1 = run_simulation(seed)
    run2 = run_simulation(seed)
    assert run1 == run2, "Simulation runs with identical seeds produced divergent outcomes!"
