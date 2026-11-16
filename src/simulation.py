"""Deterministic end-to-end simulation runner and scenario harness (L11)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .common.interfaces import (
    DOMAIN_HEADER,
    DOMAIN_TX,
    DOMAIN_VOTE,
    MSG_HEADER,
    MSG_VOTE,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    BlockHeader,
    BlockPolicy,
    Transaction,
    Vote,
)
from .consensus.codec import pack_message
from .consensus.node import ValidatorNode
from .crypto import ValidatorIdentity, ValidatorKeyRegistry, sign
from .execution import (
    MempoolConfig,
    compute_header_hash,
    compute_tx_hash,
    compute_tx_root,
    tx_signing_payload,
)
from .network import NetworkConfig, NetworkSimulator, RateLimiter, RateLimiterConfig
from .observability import DeterministicLogger
from .security import ByzantineAttackSimulator, validate_bft_validator_set, vote_signing_bytes

SUPPORTED_SCENARIOS = (
    "normal",
    "duplicate_reorder",
    "invalid_signature",
    "replay",
    "partial_synchrony",
    "silent_proposer",
    "byzantine_equivocation",
    "determinism",
)


@dataclass(frozen=True)
class SimulationConfig:
    """Validated simulator configuration parsed from JSON or defaults."""

    chain_id: str = "lab01"
    validator_count: int = 10
    random_seed: int = 42
    network: NetworkConfig = field(
        default_factory=lambda: NetworkConfig(gst=0.0, delta=0.2, min_delay=0.05, max_delay_pre_gst=0.2)
    )
    mempool: MempoolConfig = field(default_factory=MempoolConfig)
    block: BlockPolicy = field(default_factory=BlockPolicy)


@dataclass(frozen=True)
class ScenarioSummary:
    """Structured result returned by CLI scenario execution and verification."""

    scenario: str
    passed: bool
    validator_count: int
    byzantine_tolerance_f: int
    quorum: int
    seed: int
    sim_time: float
    finalized_heights: dict[str, int]
    finalized_block_hashes: list[str]
    final_state_hash: str
    committed_txs: int
    log_sha256: str
    log_path: str | None = None
    details: dict[str, Any] | None = None


def load_simulation_config(
    config_path: str | Path | None = None,
    *,
    seed_override: int | None = None,
    validator_count_override: int | None = None,
) -> SimulationConfig:
    """Load JSON config and enforce ``n = 3f + 1`` validation upfront."""
    raw: dict[str, Any] = {}
    if config_path is not None:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))

    chain_id = str(raw.get("chain_id", "lab01"))
    validator_count = (
        validator_count_override
        if validator_count_override is not None
        else int(raw.get("validator_count", 10))
    )
    validators = [f"node{i}" for i in range(validator_count)]
    validate_bft_validator_set(validators)

    seed = seed_override if seed_override is not None else int(raw.get("random_seed", 42))

    net_raw = raw.get("network", {})
    network = NetworkConfig(
        gst=float(net_raw.get("gst", 0.0)),
        delta=float(net_raw.get("delta", 0.2)),
        min_delay=float(net_raw.get("min_delay", 0.05)),
        max_delay_pre_gst=float(net_raw.get("max_delay_pre_gst", 0.3)),
        drop_rate_pre_gst=float(net_raw.get("drop_rate_pre_gst", 0.0)),
        dup_rate_pre_gst=float(net_raw.get("dup_rate_pre_gst", 0.0)),
        seed=seed,
    )

    mem_raw = raw.get("mempool", {})
    tx_ttl_val = mem_raw.get("tx_ttl")
    mempool = MempoolConfig(
        max_txs=int(mem_raw.get("max_txs", 1000)),
        max_bytes=int(mem_raw.get("max_bytes", 1_048_576)),
        max_tx_bytes=int(mem_raw.get("max_tx_bytes", 16_384)),
        tx_ttl=float(tx_ttl_val) if tx_ttl_val is not None else None,
        future_nonce_window=int(mem_raw.get("future_nonce_window", 64)),
    )

    blk_raw = raw.get("block", {})
    block = BlockPolicy(
        max_txs=int(blk_raw.get("max_txs", 100)),
        max_bytes=int(blk_raw.get("max_bytes", 262_144)),
    )

    return SimulationConfig(
        chain_id=chain_id,
        validator_count=validator_count,
        random_seed=seed,
        network=network,
        mempool=mempool,
        block=block,
    )


def derive_deterministic_keypair(identity: str) -> tuple[bytes, bytes]:
    """Derive a reproducible Ed25519 keypair from ``identity`` for test/CLI runs."""
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed_bytes = hashlib.sha256(f"lab01-key:{identity}".encode()).digest()
    priv = Ed25519PrivateKey.from_private_bytes(seed_bytes)
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return priv_raw, pub_raw


def sign_tx(
    sender: str,
    priv_key: bytes,
    key: str,
    value: str,
    nonce: int,
    chain_id: str = "lab01",
) -> Transaction:
    """Create a canonically signed Transaction under ``TX:<chain_id>``."""
    unsigned = Transaction(sender=sender, key=key, value=value, nonce=nonce)
    sig = sign(priv_key, f"{DOMAIN_TX}:{chain_id}", tx_signing_payload(unsigned))
    return Transaction(sender=sender, key=key, value=value, nonce=nonce, sig=sig)


def build_integrated_cluster(
    *,
    validator_count: int = 10,
    chain_id: str = "lab01",
    seed: int = 42,
    network_config: NetworkConfig | None = None,
    active_ids: Sequence[str] | None = None,
    participants: Sequence[str] = ("Alice", "Bob", "Mallory"),
    mempool_config: MempoolConfig | None = None,
    block_policy: BlockPolicy | None = None,
    rate_limiter_config: RateLimiterConfig | None = None,
    timeout_propose: float = 2.0,
    timeout_prevote: float = 1.0,
    timeout_precommit: float = 1.0,
) -> tuple[
    list[str],
    dict[str, ValidatorNode],
    NetworkSimulator,
    DeterministicLogger,
    dict[str, bytes],
    dict[str, bytes],
]:
    """Build a 5/5 real integrated ValidatorNode cluster over NetworkSimulator."""
    validators = [f"node{i}" for i in range(validator_count)]
    validate_bft_validator_set(validators)

    shared_logger = DeterministicLogger()
    net_cfg = network_config if network_config is not None else NetworkConfig(gst=0.0, delta=0.2, seed=seed)
    rl_cfg = (
        rate_limiter_config
        if rate_limiter_config is not None
        else RateLimiterConfig(
            max_requests_per_window=500,
            window_seconds=1.0,
            block_duration=5.0,
            max_outbound_per_window=1000,
        )
    )
    sim = NetworkSimulator(
        seed=net_cfg.seed,
        config=net_cfg,
        nodes=validators,
        rate_limiter=RateLimiter(rl_cfg, logger=shared_logger),
        logger=shared_logger,
    )

    priv_keys: dict[str, bytes] = {}
    identities: list[ValidatorIdentity] = []
    for val in validators:
        priv, pub = derive_deterministic_keypair(f"{chain_id}:{val}")
        priv_keys[val] = priv
        identities.append(ValidatorIdentity(val, pub))

    registry = ValidatorKeyRegistry(tuple(identities))
    pub_keys: dict[str, bytes] = {val: registry.get_public_key(val) for val in validators}

    participant_privs: dict[str, bytes] = {}
    for name in participants:
        p_priv, p_pub = derive_deterministic_keypair(f"{chain_id}:user:{name}")
        participant_privs[name] = p_priv
        pub_keys[name] = p_pub

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
            timeout_propose=timeout_propose,
            timeout_prevote=timeout_prevote,
            timeout_precommit=timeout_precommit,
            mempool_config=mempool_config,
            block_policy=block_policy,
        )

    return validators, nodes, sim, shared_logger, priv_keys, participant_privs


def _first_non_proposer(validators: Sequence[str], height: int = 1, round_num: int = 0) -> str:
    proposer = validators[(height + round_num) % len(validators)]
    for val in validators:
        if val != proposer:
            return val
    raise RuntimeError("Need at least 2 validators")


def _write_log_if_requested(logger: DeterministicLogger, output_path: Path | None) -> str | None:
    if output_path is None:
        return None
    target = output_path / "run.log" if output_path.suffix == "" else output_path
    written = logger.write(target)
    return str(written)


def run_scenario(
    scenario: str,
    config: SimulationConfig | None = None,
    *,
    output_path: str | Path | None = None,
) -> ScenarioSummary:
    """Execute one named end-to-end simulator scenario over the integrated stack."""
    if scenario not in SUPPORTED_SCENARIOS:
        raise ValueError(f"Unsupported scenario {scenario!r}; expected one of {SUPPORTED_SCENARIOS}")

    cfg = config if config is not None else SimulationConfig()
    out_path = Path(output_path) if output_path is not None else None
    n = cfg.validator_count
    f, quorum = validate_bft_validator_set([f"node{i}" for i in range(n)])

    if scenario == "determinism":
        first = run_scenario("partial_synchrony", cfg)
        second = run_scenario("partial_synchrony", cfg)
        log_path_str: str | None = None
        if out_path is not None:
            out_path.mkdir(parents=True, exist_ok=True)
            report_file = out_path / "determinism_summary.json"
            report_file.write_text(
                json.dumps(
                    {
                        "first_log_sha256": first.log_sha256,
                        "second_log_sha256": second.log_sha256,
                        "first_state_hash": first.final_state_hash,
                        "second_state_hash": second.final_state_hash,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            log_path_str = str(report_file)
        passed = (
            first.passed
            and second.passed
            and first.log_sha256 == second.log_sha256
            and first.final_state_hash == second.final_state_hash
            and first.finalized_block_hashes == second.finalized_block_hashes
        )
        return ScenarioSummary(
            scenario="determinism",
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=first.sim_time,
            finalized_heights=first.finalized_heights,
            finalized_block_hashes=first.finalized_block_hashes,
            final_state_hash=first.final_state_hash,
            committed_txs=first.committed_txs,
            log_sha256=first.log_sha256,
            log_path=log_path_str,
            details={"run_1_sha256": first.log_sha256, "run_2_sha256": second.log_sha256},
        )

    if scenario == "normal":
        net_cfg = NetworkConfig(gst=0.0, delta=0.2, min_delay=0.05, max_delay_pre_gst=0.2, seed=cfg.random_seed)
        validators, nodes, sim, logger, _, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        ingress = _first_non_proposer(validators)
        tx1 = sign_tx("Alice", users["Alice"], "Alice/message", "hello", 1, cfg.chain_id)
        tx2 = sign_tx("Bob", users["Bob"], "Bob/message", "hi", 1, cfg.chain_id)
        assert nodes[ingress].submit_transaction(tx1)
        assert nodes[ingress].submit_transaction(tx2)
        for node in nodes.values():
            node.start(0.0)
        sim.run_until(3.0)
        state_hashes = {node.state.get_state_hash().hex() for node in nodes.values()}
        heights = {val: node.ledger.height for val, node in nodes.items()}
        blk1_hashes = {compute_header_hash(node.ledger.get_block(1)[0]).hex() for node in nodes.values() if node.ledger.get_block(1)}
        passed = (
            min(heights.values()) >= 1
            and len(state_hashes) == 1
            and len(blk1_hashes) == 1
            and all(node.state.get("Alice/message") == "hello" and node.state.get("Bob/message") == "hi" for node in nodes.values())
        )
        ref_node = nodes[validators[0]]
        committed_txs = sum(len(b.transactions) for _, b in ref_node.ledger.get_chain_history()[1:])
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights=heights,
            finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref_node.ledger.get_chain_history()[1:]],
            final_state_hash=ref_node.state.get_state_hash().hex(),
            committed_txs=committed_txs,
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
        )

    if scenario == "duplicate_reorder":
        net_cfg = NetworkConfig(
            gst=2.0,
            delta=0.2,
            min_delay=0.05,
            max_delay_pre_gst=0.35,
            drop_rate_pre_gst=0.0,
            dup_rate_pre_gst=0.5,
            seed=cfg.random_seed,
        )
        validators, nodes, sim, logger, _, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        ingress = _first_non_proposer(validators)
        tx = sign_tx("Alice", users["Alice"], "Alice/val", "dedup-ok", 1, cfg.chain_id)
        nodes[ingress].submit_transaction(tx)
        for node in nodes.values():
            node.start(0.0)
        sim.run_until(4.0)
        state_hashes = {node.state.get_state_hash().hex() for node in nodes.values()}
        heights = {val: node.ledger.height for val, node in nodes.items()}
        passed = (
            min(heights.values()) >= 1
            and len(state_hashes) == 1
            and sim.total_duplicates > 0
            and all(node.state.get("Alice/val") == "dedup-ok" for node in nodes.values())
        )
        ref_node = nodes[validators[0]]
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights=heights,
            finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref_node.ledger.get_chain_history()[1:]],
            final_state_hash=ref_node.state.get_state_hash().hex(),
            committed_txs=sum(len(b.transactions) for _, b in ref_node.ledger.get_chain_history()[1:]),
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
            details={"duplicates_injected": sim.total_duplicates},
        )

    if scenario == "invalid_signature":
        net_cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.05, max_delay_pre_gst=0.1, seed=cfg.random_seed)
        validators, nodes, sim, logger, priv_keys, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        for node in nodes.values():
            node.start(0.0)
        target = nodes[validators[0]]
        bad_tx = Transaction("Alice", "Alice/bad", "1", 1, sig=b"\x01" * 64)
        wrong_domain_vote = Vote(
            height=1,
            round=0,
            phase=PHASE_PREVOTE,
            block_hash=b"\xaa" * 32,
            voter=validators[1],
            sig=sign(
                priv_keys[validators[1]],
                f"{DOMAIN_HEADER}:{cfg.chain_id}",
                vote_signing_bytes(Vote(1, 0, PHASE_PREVOTE, b"\xaa" * 32, validators[1])),
            ),
        )
        bad_hdr = BlockHeader(1, 0, b"\x00" * 32, b"\x11" * 32, b"\x22" * 32, validators[1], sig=b"\x02" * 64)
        sim.send(validators[1], validators[0], pack_message(MSG_VOTE, wrong_domain_vote))
        sim.send(validators[1], validators[0], pack_message(MSG_HEADER, bad_hdr))
        sim.send(validators[1], validators[0], b"not-valid-json-packet")
        target.submit_transaction(bad_tx)
        sim.run_until(0.5)
        reject_events = [r for r in logger.records if r.event_type.startswith("REJECT_")]
        event_types_seen = {r.event_type for r in reject_events}
        passed = (
            len(target.fsm.mempool) == 0
            and {"REJECT_TX", "REJECT_VOTE", "REJECT_HEADER", "REJECT_PACKET"}.issubset(event_types_seen)
        )
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights={v: node.ledger.height for v, node in nodes.items()},
            finalized_block_hashes=[],
            final_state_hash=target.state.get_state_hash().hex(),
            committed_txs=0,
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
            details={"rejection_events": sorted(event_types_seen)},
        )

    if scenario == "replay":
        net_cfg = NetworkConfig(gst=0.0, delta=0.15, min_delay=0.05, max_delay_pre_gst=0.15, seed=cfg.random_seed)
        validators, nodes, sim, logger, _, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        ingress = _first_non_proposer(validators)
        tx = sign_tx("Alice", users["Alice"], "Alice/counter", "1", 1, cfg.chain_id)
        assert nodes[ingress].submit_transaction(tx)
        assert not nodes[ingress].submit_transaction(tx)
        for node in nodes.values():
            node.start(0.0)
        sim.run_until(2.5)
        assert not nodes[ingress].submit_transaction(tx)
        sim.run_until(3.5)
        state_hashes = {node.state.get_state_hash().hex() for node in nodes.values()}
        heights = {v: node.ledger.height for v, node in nodes.items()}
        tx_h = compute_tx_hash(tx)
        applied_counts = [
            sum(
                1
                for _, body in node.ledger.get_chain_history()[1:]
                for item in body.transactions
                if compute_tx_hash(item) == tx_h
            )
            for node in nodes.values()
        ]
        passed = len(state_hashes) == 1 and min(heights.values()) >= 1 and all(c == 1 for c in applied_counts)
        ref_node = nodes[validators[0]]
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights=heights,
            finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref_node.ledger.get_chain_history()[1:]],
            final_state_hash=ref_node.state.get_state_hash().hex(),
            committed_txs=1,
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
        )

    if scenario == "partial_synchrony":
        net_cfg = NetworkConfig(
            gst=2.5,
            delta=0.2,
            min_delay=0.05,
            max_delay_pre_gst=0.6,
            drop_rate_pre_gst=0.35,
            dup_rate_pre_gst=0.2,
            seed=cfg.random_seed,
        )
        validators, nodes, sim, logger, _, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        ingress = _first_non_proposer(validators)
        tx = sign_tx("Alice", users["Alice"], "Alice/status", "post-gst-final", 1, cfg.chain_id)
        nodes[ingress].submit_transaction(tx)
        for node in nodes.values():
            node.start(0.0)
        sim.run_until(net_cfg.gst)
        pre_gst_h1 = {
            compute_header_hash(node.ledger.get_block(1)[0]).hex()
            for node in nodes.values()
            if node.ledger.get_block(1) is not None
        }
        sim.run_until(12.0)
        post_gst_h1 = {
            compute_header_hash(node.ledger.get_block(1)[0]).hex()
            for node in nodes.values()
            if node.ledger.get_block(1) is not None
        }
        heights = {v: node.ledger.height for v, node in nodes.items()}
        state_hashes = {node.state.get_state_hash().hex() for node in nodes.values()}
        passed = (
            len(pre_gst_h1) <= 1
            and len(post_gst_h1) == 1
            and min(heights.values()) >= 1
            and len(state_hashes) == 1
            and all(node.state.get("Alice/status") == "post-gst-final" for node in nodes.values())
        )
        ref_node = nodes[validators[0]]
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights=heights,
            finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref_node.ledger.get_chain_history()[1:]],
            final_state_hash=ref_node.state.get_state_hash().hex(),
            committed_txs=sum(len(b.transactions) for _, b in ref_node.ledger.get_chain_history()[1:]),
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
            details={"pre_gst_dropped": sim.total_dropped},
        )

    if scenario == "silent_proposer":
        net_cfg = NetworkConfig(gst=0.0, delta=0.2, min_delay=0.05, max_delay_pre_gst=0.2, seed=cfg.random_seed)
        validators = [f"node{i}" for i in range(n)]
        silent_proposer = validators[1]  # proposer for (H=1, R=0)
        honest = [v for v in validators if v != silent_proposer]
        _, nodes, sim, logger, _, users = build_integrated_cluster(
            validator_count=n,
            chain_id=cfg.chain_id,
            seed=cfg.random_seed,
            network_config=net_cfg,
            active_ids=honest,
            mempool_config=cfg.mempool,
            block_policy=cfg.block,
        )
        ingress = honest[0]
        tx = sign_tx("Alice", users["Alice"], "Alice/recovery", "round-1-ok", 1, cfg.chain_id)
        nodes[ingress].submit_transaction(tx)
        for node in nodes.values():
            node.start(0.0)
        sim.run_until(8.0)
        heights = {v: node.ledger.height for v, node in nodes.items()}
        blk1_hashes = {
            compute_header_hash(node.ledger.get_block(1)[0]).hex()
            for node in nodes.values()
            if node.ledger.get_block(1) is not None
        }
        passed = (
            min(heights.values()) >= 1
            and len(blk1_hashes) == 1
            and all(node.state.get("Alice/recovery") == "round-1-ok" for node in nodes.values())
        )
        ref_node = nodes[honest[0]]
        return ScenarioSummary(
            scenario=scenario,
            passed=passed,
            validator_count=n,
            byzantine_tolerance_f=f,
            quorum=quorum,
            seed=cfg.random_seed,
            sim_time=sim.current_time,
            finalized_heights=heights,
            finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref_node.ledger.get_chain_history()[1:]],
            final_state_hash=ref_node.state.get_state_hash().hex(),
            committed_txs=sum(len(b.transactions) for _, b in ref_node.ledger.get_chain_history()[1:]),
            log_sha256=logger.digest(),
            log_path=_write_log_if_requested(logger, out_path),
            details={"silent_proposer": silent_proposer},
        )

    # scenario == "byzantine_equivocation"
    # Up to f Byzantine validators equivocate across honest halves while honest proposer
    # (node1) drives valid block A -> honest validators detect equivocation, never finalize B,
    # and all honest nodes converge cleanly on block A.
    net_cfg = NetworkConfig(gst=0.0, delta=0.15, min_delay=0.05, max_delay_pre_gst=0.15, seed=cfg.random_seed)
    validators = [f"node{i}" for i in range(n)]
    honest_proposer = validators[1]
    non_proposers = [v for v in validators if v != honest_proposer]
    byz_nodes = non_proposers[:f]  # Up to f Byzantine validators
    honest = [v for v in validators if v not in byz_nodes]
    _, nodes, sim, logger, priv_keys, users = build_integrated_cluster(
        validator_count=n,
        chain_id=cfg.chain_id,
        seed=cfg.random_seed,
        network_config=net_cfg,
        active_ids=honest,
        mempool_config=cfg.mempool,
        block_policy=cfg.block,
    )
    attack = ByzantineAttackSimulator(validators, byz_nodes)
    tx_a = sign_tx("Alice", users["Alice"], "Alice/fork_test", "branch-A", 1, cfg.chain_id)
    tx_b = sign_tx("Bob", users["Bob"], "Bob/fork_test", "branch-B", 1, cfg.chain_id)
    for val in honest:
        nodes[val].submit_transaction(tx_a)
        nodes[val].start(0.0)

    ref = nodes[honest_proposer]
    parent = ref.ledger.get_latest_hash()
    _, sh_a = ref.state.validate_and_compute_hash((tx_a,))
    _, sh_b = ref.state.validate_and_compute_hash((tx_b,))
    hdr_a = BlockHeader(1, 0, parent, sh_a, compute_tx_root([compute_tx_hash(tx_a)]), honest_proposer)
    hdr_b = BlockHeader(1, 0, parent, sh_b, compute_tx_root([compute_tx_hash(tx_b)]), honest_proposer)
    hash_a = compute_header_hash(hdr_a)
    hash_b = compute_header_hash(hdr_b)

    for b_node in byz_nodes:
        for phase in (PHASE_PREVOTE, PHASE_PRECOMMIT):
            vote_a = Vote(1, 0, phase, hash_a, b_node)
            directed = attack.equivocate(b_node, vote_a, hash_b, recipients=honest)
            for item in directed:
                signed_v = Vote(
                    item.vote.height,
                    item.vote.round,
                    item.vote.phase,
                    item.vote.block_hash,
                    item.vote.voter,
                    sig=sign(
                        priv_keys[b_node],
                        f"{DOMAIN_VOTE}:{cfg.chain_id}",
                        vote_signing_bytes(item.vote),
                    ),
                )
                sim.send(b_node, item.receiver, pack_message(MSG_VOTE, signed_v))
            # Also deliver the conflicting vote to honest[0] so direct equivocation
            # detection & rejection logging is exercised in the same run.
            conflict_vote = Vote(1, 0, phase, hash_b, b_node)
            signed_conflict = Vote(
                conflict_vote.height,
                conflict_vote.round,
                conflict_vote.phase,
                conflict_vote.block_hash,
                conflict_vote.voter,
                sig=sign(
                    priv_keys[b_node],
                    f"{DOMAIN_VOTE}:{cfg.chain_id}",
                    vote_signing_bytes(conflict_vote),
                ),
            )
            sim.send(b_node, honest[0], pack_message(MSG_VOTE, signed_conflict))

    sim.run_until(6.0)
    blk1_hashes = {
        compute_header_hash(node.ledger.get_block(1)[0]).hex()
        for node in nodes.values()
        if node.ledger.get_block(1) is not None
    }
    heights = {v: node.ledger.height for v, node in nodes.items()}
    state_hashes = {node.state.get_state_hash().hex() for node in nodes.values()}
    equivocation_detected = any(len(node.vote_tracker.equivocations) > 0 for node in nodes.values())
    passed = (
        len(blk1_hashes) == 1
        and min(heights.values()) >= 1
        and len(state_hashes) == 1
        and equivocation_detected
    )
    return ScenarioSummary(
        scenario=scenario,
        passed=passed,
        validator_count=n,
        byzantine_tolerance_f=f,
        quorum=quorum,
        seed=cfg.random_seed,
        sim_time=sim.current_time,
        finalized_heights=heights,
        finalized_block_hashes=[compute_header_hash(h).hex() for h, _ in ref.ledger.get_chain_history()[1:]],
        final_state_hash=ref.state.get_state_hash().hex(),
        committed_txs=sum(len(b.transactions) for _, b in ref.ledger.get_chain_history()[1:]),
        log_sha256=logger.digest(),
        log_path=_write_log_if_requested(logger, out_path),
        details={"byzantine_nodes": byz_nodes, "equivocation_detected": equivocation_detected},
    )


def summary_to_dict(summary: ScenarioSummary) -> dict[str, Any]:
    return asdict(summary)


__all__ = [
    "SUPPORTED_SCENARIOS",
    "ScenarioSummary",
    "SimulationConfig",
    "build_integrated_cluster",
    "derive_deterministic_keypair",
    "load_simulation_config",
    "run_scenario",
    "sign_tx",
    "summary_to_dict",
]
