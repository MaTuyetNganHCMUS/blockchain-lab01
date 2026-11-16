"""Full 10-validator integrated E2E tests covering T1-T8, Header-before-Body, TX Gossip, and CLI."""

from __future__ import annotations

from pathlib import Path

from src.common.interfaces import (
    DOMAIN_HEADER,
    DOMAIN_VOTE,
    EVENT_FINALIZE,
    EVENT_REJECT_BODY,
    EVENT_REJECT_HEADER,
    EVENT_REJECT_PACKET,
    EVENT_REJECT_TX,
    EVENT_REJECT_VOTE,
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    REASON_INVALID_SIGNATURE,
    REASON_MALFORMED_PACKET,
    REASON_TX_BAD_SIGNATURE,
    REASON_UNACCEPTED_HEADER,
    BlockBody,
    BlockHeader,
    Transaction,
    Vote,
)
from src.consensus.codec import pack_message
from src.crypto import sign
from src.execution import compute_header_hash, compute_tx_hash
from src.network import NetworkConfig
from src.security import ByzantineAttackSimulator, vote_signing_bytes
from src.simulation import (
    build_integrated_cluster,
    load_simulation_config,
    run_scenario,
    sign_tx,
)


def test_full_t1_10_validators_non_proposer_tx_gossip_and_header_before_body() -> None:
    """T1 + L03 + L07 + L08: 10 real integrated validators, TX sent to non-proposer,
    Header delivered & accepted before Body is ever sent or delivered."""
    net_cfg = NetworkConfig(gst=0.0, delta=0.2, min_delay=0.05, max_delay_pre_gst=0.2, seed=42)
    validators, nodes, sim, logger, _, users = build_integrated_cluster(
        validator_count=10,
        network_config=net_cfg,
    )
    proposer = nodes["node0"].fsm.get_proposer(1, 0)
    non_proposer = next(v for v in validators if v != proposer)

    tx1 = sign_tx("Alice", users["Alice"], "Alice/message", "hello", 1)
    tx2 = sign_tx("Bob", users["Bob"], "Bob/message", "hi", 1)
    assert nodes[non_proposer].submit_transaction(tx1)
    assert nodes[non_proposer].submit_transaction(tx2)
    # Proposer mempool starts empty before network runs
    assert len(nodes[proposer].fsm.mempool) == 0

    for node in nodes.values():
        node.start(0.0)
    sim.run_until(4.0)

    # All 10 validators converged with both transactions committed
    state_hashes = {n.state.get_state_hash() for n in nodes.values()}
    assert len(state_hashes) == 1
    for val, node in nodes.items():
        assert node.ledger.height >= 1, f"{val} did not finalize"
        assert node.state.get("Alice/message") == "hello"
        assert node.state.get("Bob/message") == "hi"

    # Verify Header-before-Body ordering in network logs:
    # For every peer receiver, first HEADER send to peer precedes BODY_REQUEST sent by peer,
    # which precedes BODY send to peer.
    first_header_to: dict[str, float] = {}
    first_body_req_from: dict[str, float] = {}
    first_body_to: dict[str, float] = {}
    for rec in logger.records:
        if rec.event_type != "SEND":
            continue
        if f"Send {MSG_HEADER} to " in rec.detail:
            target = rec.detail.split(f"Send {MSG_HEADER} to ", 1)[1].split(" ", 1)[0]
            first_header_to.setdefault(target, float(rec.sim_time))
        elif f"Send {MSG_BODY_REQUEST} to " in rec.detail:
            first_body_req_from.setdefault(rec.node_id, float(rec.sim_time))
        elif f"Send {MSG_BODY} to " in rec.detail:
            target = rec.detail.split(f"Send {MSG_BODY} to ", 1)[1].split(" ", 1)[0]
            first_body_to.setdefault(target, float(rec.sim_time))

    assert len(first_body_to) > 0
    for peer, body_t in first_body_to.items():
        assert peer in first_header_to
        assert peer in first_body_req_from
        assert first_header_to[peer] <= first_body_req_from[peer] <= body_t


def test_full_t2_duplicate_and_reordered_packets_10_validators() -> None:
    """T2: 10 real integrated validators under heavy duplication & reordering."""
    summary = run_scenario("duplicate_reorder", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True
    assert summary.validator_count == 10
    assert summary.quorum == 7
    assert summary.details is not None
    assert summary.details["duplicates_injected"] > 0


def test_full_t3_invalid_signatures_domains_and_rejection_logging() -> None:
    """T3 + L09: Forged signatures, wrong domain, and malformed packets logged with reason codes."""
    summary = run_scenario("invalid_signature", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True

    # Also assert explicit reason codes via direct integrated check
    _validators, nodes, sim, logger, priv_keys, _ = build_integrated_cluster(validator_count=10)
    for n in nodes.values():
        n.start(0.0)
    target = nodes["node0"]

    bad_tx = Transaction("Alice", "Alice/x", "1", 1, sig=b"\x01" * 64)
    target.submit_transaction(bad_tx)
    wrong_domain_vote = Vote(
        1, 0, PHASE_PREVOTE, b"\xaa" * 32, "node1",
        sig=sign(
            priv_keys["node1"],
            f"{DOMAIN_HEADER}:lab01",
            vote_signing_bytes(Vote(1, 0, PHASE_PREVOTE, b"\xaa" * 32, "node1")),
        ),
    )
    bad_hdr = BlockHeader(1, 0, b"\x00" * 32, b"\x11" * 32, b"\x22" * 32, "node1", sig=b"\x03" * 64)
    unsolicited_body = BlockBody(header_hash=b"\x55" * 32, transactions=())

    sim.send("node1", "node0", pack_message("VOTE", wrong_domain_vote))
    sim.send("node1", "node0", pack_message(MSG_HEADER, bad_hdr))
    sim.send("node1", "node0", pack_message(MSG_BODY, unsolicited_body))
    sim.send("node1", "node0", b"corrupt-wire-payload")
    sim.run_until(0.5)

    by_type = {r.event_type: [] for r in logger.records}
    for r in logger.records:
        by_type.setdefault(r.event_type, []).append(r.detail)

    assert any(REASON_TX_BAD_SIGNATURE in d for d in by_type.get(EVENT_REJECT_TX, []))
    assert any(REASON_INVALID_SIGNATURE in d for d in by_type.get(EVENT_REJECT_VOTE, []))
    assert any(REASON_INVALID_SIGNATURE in d for d in by_type.get(EVENT_REJECT_HEADER, []))
    assert any(REASON_UNACCEPTED_HEADER in d for d in by_type.get(EVENT_REJECT_BODY, []))
    assert any(REASON_MALFORMED_PACKET in d for d in by_type.get(EVENT_REJECT_PACKET, []))
    assert len(target.fsm.mempool) == 0


def test_full_t4_replay_protection_before_and_after_finality() -> None:
    """T4: Replayed TX before and after finality is applied at most once."""
    summary = run_scenario("replay", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True
    assert summary.committed_txs == 1


def test_full_t5_partial_synchrony_no_cache_injection_10_validators() -> None:
    """T5: 10 real integrated validators under pre-GST drop/jitter without direct cache writes."""
    summary = run_scenario("partial_synchrony", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True
    assert summary.validator_count == 10
    assert min(summary.finalized_heights.values()) >= 1
    assert summary.details is not None
    assert summary.details["pre_gst_dropped"] > 0


def test_full_t6_silent_proposer_10_validators_real_network() -> None:
    """T6: Silent proposer at (H=1, R=0) triggers timeout and round advance over NetworkSimulator."""
    summary = run_scenario("silent_proposer", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True
    assert min(summary.finalized_heights.values()) >= 1


def test_full_t7_byzantine_equivocation_with_real_precommit_finality() -> None:
    """T7: Up to f=3 Byzantine validators equivocate across 10-validator network -> no conflicting fork."""
    summary = run_scenario("byzantine_equivocation", load_simulation_config(Path("config/default.json")))
    assert summary.passed is True
    assert summary.byzantine_tolerance_f == 3
    assert summary.quorum == 7
    assert summary.details is not None
    assert summary.details["equivocation_detected"] is True


def test_full_t7_byzantine_proposer_splits_honest_halves_no_double_finalization() -> None:
    """T7 (adversarial split): Byzantine proposer + 2 Byzantine accomplices send conflicting
    blocks A and B to honest subsets -> at most one block can ever finalize at height 1."""
    cfg = NetworkConfig(gst=0.0, delta=0.1, min_delay=0.05, max_delay_pre_gst=0.1, seed=99)
    validators = [f"node{i}" for i in range(10)]
    byz_nodes = ["node0", "node1", "node2"]  # node1 is proposer at (H=1, R=0)
    honest = validators[3:]
    _, nodes, sim, logger, priv_keys, users = build_integrated_cluster(
        validator_count=10,
        network_config=cfg,
        active_ids=honest,
    )
    for val in honest:
        nodes[val].start(0.0)

    # Register minimal responder for Byzantine proposer node1 to serve bodies when requested
    ref = nodes["node3"]
    tx_a = sign_tx("Alice", users["Alice"], "Alice/split", "A", 1)
    tx_b = sign_tx("Bob", users["Bob"], "Bob/split", "B", 1)
    _, sh_a = ref.state.validate_and_compute_hash((tx_a,))
    _, sh_b = ref.state.validate_and_compute_hash((tx_b,))
    from src.consensus.codec import unpack_message
    from src.execution import compute_tx_root, header_signing_payload

    uh_a = BlockHeader(1, 0, ref.ledger.get_latest_hash(), sh_a, compute_tx_root([compute_tx_hash(tx_a)]), "node1")
    uh_b = BlockHeader(1, 0, ref.ledger.get_latest_hash(), sh_b, compute_tx_root([compute_tx_hash(tx_b)]), "node1")
    hdr_a = BlockHeader(1, 0, uh_a.parent_hash, sh_a, uh_a.tx_root, "node1",
                        sig=sign(priv_keys["node1"], f"{DOMAIN_HEADER}:lab01", header_signing_payload(uh_a)))
    hdr_b = BlockHeader(1, 0, uh_b.parent_hash, sh_b, uh_b.tx_root, "node1",
                        sig=sign(priv_keys["node1"], f"{DOMAIN_HEADER}:lab01", header_signing_payload(uh_b)))
    hash_a = compute_header_hash(hdr_a)
    hash_b = compute_header_hash(hdr_b)
    bodies = {hash_a: BlockBody(hash_a, (tx_a,)), hash_b: BlockBody(hash_b, (tx_b,))}

    class ByzResponder:
        height = 1

        def handle_packet(self, sim_time: float, raw: bytes, sender: str = "") -> None:
            mtype, payload = unpack_message(raw)
            if mtype == MSG_BODY_REQUEST and payload in bodies and sender:
                sim.send("node1", sender, pack_message(MSG_BODY, bodies[payload]))

    sim.register_node("node1", ByzResponder())

    for h_val in ("node3", "node4", "node5", "node6"):
        sim.send("node1", h_val, pack_message(MSG_HEADER, hdr_a))
    for h_val in ("node7", "node8", "node9"):
        sim.send("node1", h_val, pack_message(MSG_HEADER, hdr_b))

    attack = ByzantineAttackSimulator(validators, byz_nodes)
    for b_node in byz_nodes:
        for phase in (PHASE_PREVOTE, PHASE_PRECOMMIT):
            for item in attack.equivocate(b_node, Vote(1, 0, phase, hash_a, b_node), hash_b, recipients=honest):
                target_hash = hash_a if item.receiver in ("node3", "node4", "node5", "node6") else hash_b
                uv = Vote(1, 0, phase, target_hash, b_node)
                sv = Vote(1, 0, phase, target_hash, b_node,
                          sig=sign(priv_keys[b_node], f"{DOMAIN_VOTE}:lab01", vote_signing_bytes(uv)))
                sim.send(b_node, item.receiver, pack_message("VOTE", sv))

    sim.run_until(4.0)

    finalized_h1 = {
        compute_header_hash(node.ledger.get_block(1)[0])
        for node in nodes.values()
        if node.ledger.get_block(1) is not None
    }
    assert len(finalized_h1) <= 1, "Safety violation: two distinct blocks finalized at height 1!"
    assert hash_b not in finalized_h1
    # Any finalization must correspond to actual FINALIZE log events from PRECOMMIT quorum
    finalize_logs = [r for r in logger.records if r.event_type == EVENT_FINALIZE]
    if finalized_h1:
        assert len(finalize_logs) > 0


def test_full_t8_determinism_10_validators_byte_identical_logs_and_state() -> None:
    """T8: Running the full 10-validator integrated partial-synchrony scenario twice with
    the same seed produces byte-identical logs and identical final state hash."""
    cfg = load_simulation_config(Path("config/default.json"))
    summary = run_scenario("determinism", cfg)
    assert summary.passed is True
    assert summary.details is not None
    assert summary.details["run_1_sha256"] == summary.details["run_2_sha256"]

    # Also verify different seed is allowed to produce different network trace
    other = run_scenario("partial_synchrony", load_simulation_config(Path("config/default.json"), seed_override=999))
    assert other.passed is True
    assert other.log_sha256 != summary.log_sha256


def test_cli_entry_point_runs_and_rejects_invalid_validator_count(tmp_path: Path) -> None:
    from src.__main__ import main as cli_main

    log_dir = tmp_path / "run_normal"
    exit_ok = cli_main(["--scenario", "normal", "--config", "config/default.json", "--seed", "42", "--output", str(log_dir)])
    assert exit_ok == 0
    assert (log_dir / "run.log").exists()

    exit_bad = cli_main(["--validators", "8"])
    assert exit_bad == 2
