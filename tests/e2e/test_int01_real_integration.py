"""INT-01 System Integration Tests: Full 5/5 Real Module Integration (TV1 + TV2 + TV3 + TV4 + TV5).

Verifies end-to-end integration across all five team modules on ``main``:
- TV1: ``Ed25519Crypto`` & ``ValidatorKeyRegistry`` (``src/crypto/``)
- TV2: ``StateEngine`` & ``Ledger`` & ``commitment`` (``src/execution/``)
- TV3: ``NetworkSimulator`` & ``NetworkConfig`` (``src/network/``)
- TV4: ``ValidatorNode.create_integrated`` & ``ConsensusFSM`` (``src/consensus/``)
- TV5: ``VoteTracker``, ``LockManager``, & ``DeterministicLogger`` (``src/security/``, ``src/observability/``)
"""

from __future__ import annotations

from src.common.interfaces import (
    CryptoProtocol,
    LedgerProtocol,
    LockManagerProtocol,
    LoggerProtocol,
    NetworkProtocol,
    PHASE_PREVOTE,
    StateProtocol,
    Transaction,
    Vote,
    VoteTrackerProtocol,
)
from src.consensus.node import ValidatorNode
from src.crypto import Ed25519Crypto, ValidatorIdentity, ValidatorKeyRegistry
from src.execution import Ledger, StateEngine, compute_header_hash
from src.network import NetworkConfig, NetworkSimulator, RateLimiter, RateLimiterConfig
from src.observability import DeterministicLogger
from src.security import LockManager, VoteTracker


def _build_full_integrated_cluster(
    validators: list[str],
    *,
    chain_id: str = "lab01",
    active_ids: list[str] | None = None,
    seed: int = 42,
    network_config: NetworkConfig | None = None,
) -> tuple[dict[str, ValidatorNode], NetworkSimulator, DeterministicLogger]:
    crypto = Ed25519Crypto()
    shared_logger = DeterministicLogger()
    config = network_config if network_config is not None else NetworkConfig(seed=seed)
    rate_limiter = RateLimiter(
        RateLimiterConfig(
            max_requests_per_window=200,
            window_seconds=1.0,
            block_duration=5.0,
            max_outbound_per_window=400,
        ),
        logger=shared_logger,
    )
    sim = NetworkSimulator(
        seed=config.seed,
        config=config,
        rate_limiter=rate_limiter,
        logger=shared_logger,
    )

    identities: list[ValidatorIdentity] = []
    priv_keys: dict[str, bytes] = {}
    for val in validators:
        priv, pub = crypto.generate_keypair()
        priv_keys[val] = priv
        identities.append(ValidatorIdentity(validator_id=val, public_key=pub))

    registry = ValidatorKeyRegistry(tuple(identities))
    pub_keys = {val: registry.get_public_key(val) for val in validators}

    nodes: dict[str, ValidatorNode] = {}
    for val in active_ids or validators:
        nodes[val] = ValidatorNode.create_integrated(
            node_id=val,
            validators=validators,
            chain_id=chain_id,
            priv_key=priv_keys[val],
            pub_keys=pub_keys,
            network=sim,
            logger=shared_logger,
            require_vote_signatures=True,
        )
    return nodes, sim, shared_logger


def test_real_modules_satisfy_protocols_and_factory() -> None:
    validators = ["n0", "n1", "n2", "n3"]
    node = ValidatorNode.create_integrated(node_id="n0", validators=validators)

    assert isinstance(node.crypto, Ed25519Crypto)
    assert isinstance(node.crypto, CryptoProtocol)
    assert isinstance(node.state, StateEngine)
    assert isinstance(node.state, StateProtocol)
    assert isinstance(node.ledger, Ledger)
    assert isinstance(node.ledger, LedgerProtocol)
    assert isinstance(node.network, NetworkSimulator)
    assert isinstance(node.network, NetworkProtocol)
    assert isinstance(node.vote_tracker, VoteTracker)
    assert isinstance(node.vote_tracker, VoteTrackerProtocol)
    assert isinstance(node.lock_manager, LockManager)
    assert isinstance(node.lock_manager, LockManagerProtocol)
    assert isinstance(node.logger, DeterministicLogger)
    assert isinstance(node.logger, LoggerProtocol)


def test_integrated_5of5_multi_height_consensus_with_network_simulator() -> None:
    validators = ["n0", "n1", "n2", "n3"]
    cfg = NetworkConfig(
        gst=0.0,
        delta=0.2,
        min_delay=0.05,
        max_delay_pre_gst=0.2,
        drop_rate_pre_gst=0.0,
        dup_rate_pre_gst=0.0,
        seed=101,
    )
    nodes, sim, shared_logger = _build_full_integrated_cluster(validators, network_config=cfg)

    tx_alice_1 = Transaction("Alice", "Alice/balance", "100", 1, b"sig_a1")
    tx_bob_1 = Transaction("Bob", "Bob/balance", "50", 1, b"sig_b1")
    tx_invalid = Transaction("Mallory", "Alice/balance", "9999", 1, b"bad_owner")

    for node in nodes.values():
        node.submit_transaction(tx_alice_1)
        node.submit_transaction(tx_bob_1)
        node.submit_transaction(tx_invalid)

    for node in nodes.values():
        node.start(sim_time=0.0)

    # Discrete-event simulation with TV3 NetworkSimulator driving messages & timeouts
    sim.run_until(2.5)

    finalized_heights = {node.ledger.height for node in nodes.values()}
    committed_height = min(finalized_heights)
    assert committed_height >= 2

    for val, node in nodes.items():
        assert isinstance(node.ledger, Ledger)
        assert isinstance(node.state, StateEngine)
        assert node.state.get("Alice/balance") == "100"
        assert node.state.get("Bob/balance") == "50"
        assert node.state.dump_data() == {"Alice/balance": "100", "Bob/balance": "50"}
        assert isinstance(node.vote_tracker, VoteTracker)
        assert node.vote_tracker.rejected_votes == ()
        assert node.ledger.height >= committed_height, f"{val} lagged behind"

    # Compare chain commitments at the common finalized height
    blk1_hashes = {
        compute_header_hash(nodes[val].ledger.get_chain_history()[1][0]) for val in validators
    }
    blk2_parent_hashes = {
        nodes[val].ledger.get_chain_history()[2][0].parent_hash for val in validators
    }
    assert len(blk1_hashes) == 1
    assert blk2_parent_hashes == blk1_hashes
    assert len(shared_logger.records) > 0
    assert sim.total_delivered > 0


def test_integrated_5of5_partial_synchrony_timeout_and_round_change() -> None:
    validators = ["n0", "n1", "n2", "n3"]
    # n1 is crashed/silent for H=1,R=0; pre-GST jitter & loss give way to post-GST stability at t=2.5
    honest = ["n0", "n2", "n3"]
    cfg = NetworkConfig(
        gst=2.5,
        delta=0.2,
        min_delay=0.05,
        max_delay_pre_gst=0.3,
        drop_rate_pre_gst=0.25,
        dup_rate_pre_gst=0.1,
        seed=202,
    )
    nodes, sim, _ = _build_full_integrated_cluster(
        validators,
        active_ids=honest,
        network_config=cfg,
    )

    tx = Transaction("Alice", "Alice/msg", "recovered_via_tv3", 1, b"sig")
    for val in honest:
        nodes[val].submit_transaction(tx)
        nodes[val].start(sim_time=0.0)

    # No manual handle_timeout calls: TV3 NetworkSimulator dispatches 'H:R:STEP' timers automatically!
    sim.run_until(8.0)

    for val in honest:
        assert nodes[val].height >= 2, f"{val} did not recover after timeout & round change"
        assert nodes[val].finalized_count >= 1
        assert nodes[val].state.get("Alice/msg") == "recovered_via_tv3"

    blk1_hashes = {
        compute_header_hash(nodes[val].ledger.get_chain_history()[1][0]) for val in honest
    }
    assert len(blk1_hashes) == 1


def test_integrated_5of5_deterministic_replay_same_seed() -> None:
    validators = ["n0", "n1", "n2", "n3"]
    crypto = Ed25519Crypto()
    keypairs = {val: crypto.generate_keypair() for val in validators}
    pub_keys = {val: keypairs[val][1] for val in validators}
    cfg = NetworkConfig(
        gst=1.0,
        delta=0.15,
        min_delay=0.05,
        max_delay_pre_gst=0.3,
        drop_rate_pre_gst=0.1,
        dup_rate_pre_gst=0.1,
        seed=777,
    )

    digests: list[str] = []
    state_hashes_runs: list[set[bytes]] = []
    for _ in range(2):
        shared_logger = DeterministicLogger()
        sim = NetworkSimulator(
            seed=cfg.seed,
            config=cfg,
            rate_limiter=RateLimiter(RateLimiterConfig(max_requests_per_window=200, max_outbound_per_window=400), logger=shared_logger),
            logger=shared_logger,
        )
        nodes = {
            val: ValidatorNode.create_integrated(
                node_id=val,
                validators=validators,
                priv_key=keypairs[val][0],
                pub_keys=pub_keys,
                network=sim,
                logger=shared_logger,
            )
            for val in validators
        }
        tx = Transaction("Alice", "Alice/counter", "42", 1, b"s1")
        for node in nodes.values():
            node.submit_transaction(tx)
            node.start(sim_time=0.0)
        sim.run_until(2.5)

        digests.append(shared_logger.digest())
        state_hashes_runs.append({n.state.get_state_hash() for n in nodes.values()})
        assert nodes["n0"].state.get("Alice/counter") == "42"

    assert digests[0] == digests[1]
    assert len(state_hashes_runs[0]) == 1
    assert state_hashes_runs[0] == state_hashes_runs[1]


def test_integrated_rejects_forged_vote_signature() -> None:
    validators = ["n0", "n1", "n2", "n3"]
    nodes, _, _ = _build_full_integrated_cluster(validators)
    node0 = nodes["n0"]
    node0.start(sim_time=0.0)

    forged = Vote(
        height=1,
        round=0,
        phase=PHASE_PREVOTE,
        block_hash=b"X" * 32,
        voter="n1",
        sig=b"\x01" * 64,
    )
    node0.fsm.on_vote(sim_time=0.2, vote=forged)

    assert node0.round == 0
    assert (1, 0) not in node0.fsm.precommitted_rounds
    res = node0.vote_tracker.add_vote(forged)
    assert res.has_quorum is False
    assert isinstance(node0.vote_tracker, VoteTracker)
    assert node0.vote_tracker.last_rejection is not None
