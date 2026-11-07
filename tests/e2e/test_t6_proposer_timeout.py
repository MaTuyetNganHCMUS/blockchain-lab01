"""End-to-end integration test for Test T6: Proposer silent/crashed.

Scenario:
- 4 validator nodes (n=4, f=1): node0, node1, node2, node3.
- Height 1, Round 0:
    * Proposer is node1 (since (1 + 0) % 4 == 1).
    * Node1 crashes/remains silent and sends no block proposal.
    * Honest nodes (node0, node2, node3) encounter PROPOSE timeout.
    * Honest nodes cast PREVOTE NIL.
    * Quorum of PREVOTE NIL (3 >= 2f + 1) triggers PRECOMMIT NIL.
    * Quorum of PRECOMMIT NIL triggers round advance to Round 1.
- Height 1, Round 1:
    * New proposer is node2 (since (1 + 1) % 4 == 2).
    * Node2 is honest, proposes block with transaction.
    * Nodes prevote, precommit, and successfully finalize block at Height 1.
    * System achieves consensus and chain continues to Height 2.
"""

from src.common.interfaces import (
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    STEP_PROPOSE,
    Transaction,
)
from src.common.mocks import MockCrypto
from src.consensus.node import ValidatorNode


class MiniNetwork:
    """Synchronous in-memory router connecting validator nodes for E2E testing."""

    def __init__(self, nodes: dict[str, ValidatorNode]) -> None:
        self.nodes = nodes
        self.packet_queue: list[tuple[str, str, bytes]] = []

    def send(self, sender: str, receiver: str, raw_bytes: bytes) -> None:
        self.packet_queue.append((sender, receiver, raw_bytes))

    def broadcast_header(self, sender: str, header_bytes: bytes) -> None:
        for node_id in self.nodes:
            if node_id != sender:
                self.packet_queue.append((sender, node_id, header_bytes))

    def schedule_timeout(self, node_id: str, delay: float | int, callback_tag: str) -> None:
        pass

    def deliver_all(self, sim_time: float) -> int:
        """Deliver all pending packets in router queue."""
        count = 0
        current_queue = list(self.packet_queue)
        self.packet_queue.clear()
        for sender, receiver, payload in current_queue:
            target_node = self.nodes.get(receiver)
            if target_node is not None:
                target_node.handle_packet(sim_time, payload)
                count += 1
        return count


def test_t6_proposer_crashed_round_timeout_and_recovery() -> None:
    validators = ["node0", "node1", "node2", "node3"]
    crypto = MockCrypto()
    priv_keys: dict[str, bytes] = {}
    pub_keys: dict[str, bytes] = {}

    for val in validators:
        priv, pub = crypto.generate_keypair(val)
        priv_keys[val] = priv
        pub_keys[val] = pub

    # Instantiate validator nodes
    nodes: dict[str, ValidatorNode] = {}
    for val in validators:
        node = ValidatorNode(
            node_id=val,
            validators=validators,
            chain_id="lab01",
            crypto=crypto,
            priv_key=priv_keys[val],
            pub_keys=pub_keys,
            timeout_propose=2.0,
            timeout_prevote=1.0,
            timeout_precommit=1.0,
        )
        nodes[val] = node

    # Attach network router
    router = MiniNetwork(nodes)
    for node in nodes.values():
        node.fsm.network = router

    # --------------------------------------------------------------------------
    # Step 1: Initialize nodes at Height 1, Round 0
    # --------------------------------------------------------------------------
    for val in ["node0", "node2", "node3"]:
        nodes[val].start(sim_time=0.0)

    # Verify designated proposer for (H=1, R=0) is node1
    assert nodes["node0"].fsm.get_proposer(1, 0) == "node1"

    # Node1 is crashed / silent: does not start or send anything!
    assert len(router.packet_queue) == 0

    # Ingest transaction for future block
    tx = Transaction(sender="Alice", key="Alice/message", value="hello_tendermint", nonce=0)
    nodes["node2"].submit_transaction(tx)

    # --------------------------------------------------------------------------
    # Step 2: Propose timeout fires on honest nodes (node0, node2, node3)
    # --------------------------------------------------------------------------
    sim_time = 2.0
    for val in ["node0", "node2", "node3"]:
        nodes[val].handle_timeout(sim_time, height=1, round=0, step=STEP_PROPOSE)

    # Each honest node must have cast PREVOTE for NIL
    for val in ["node0", "node2", "node3"]:
        fsm = nodes[val].fsm
        assert (1, 0) in fsm.prevoted_rounds
        assert fsm.votes_cast[-1].phase == PHASE_PREVOTE
        assert fsm.votes_cast[-1].block_hash == NIL_BLOCK_HASH

    # --------------------------------------------------------------------------
    # Step 3: Network delivers PREVOTE NIL messages between honest nodes
    # --------------------------------------------------------------------------
    delivered = router.deliver_all(sim_time=2.1)
    assert delivered > 0

    # Quorum of PREVOTE NIL (3 >= 2f + 1) achieved -> Each honest node cast PRECOMMIT NIL
    for val in ["node0", "node2", "node3"]:
        fsm = nodes[val].fsm
        assert (1, 0) in fsm.precommitted_rounds
        assert fsm.votes_cast[-1].phase == PHASE_PRECOMMIT
        assert fsm.votes_cast[-1].block_hash == NIL_BLOCK_HASH

    # --------------------------------------------------------------------------
    # Step 4: Network delivers PRECOMMIT NIL messages
    # --------------------------------------------------------------------------
    router.deliver_all(sim_time=2.2)

    # Quorum of PRECOMMIT NIL achieved -> Honest nodes automatically advance to Round 1!
    for val in ["node0", "node2", "node3"]:
        fsm = nodes[val].fsm
        assert fsm.round == 1
        assert fsm.height == 1

    # --------------------------------------------------------------------------
    # Step 5: Round 1 proposal is dispatched by node2 and delivered
    # --------------------------------------------------------------------------
    assert nodes["node0"].fsm.get_proposer(1, 1) == "node2"

    # Router delivers proposal header and body from node2 to node0 and node3
    router.deliver_all(sim_time=2.3)

    # All honest nodes should now have prevoted for the proposed block
    for val in ["node0", "node2", "node3"]:
        fsm = nodes[val].fsm
        assert (1, 1) in fsm.prevoted_rounds
        assert fsm.votes_cast[-1].phase == PHASE_PREVOTE
        assert fsm.votes_cast[-1].block_hash != NIL_BLOCK_HASH

    # Deliver PREVOTEs -> Triggers Polka & PRECOMMIT on block
    router.deliver_all(sim_time=2.4)

    for val in ["node0", "node2", "node3"]:
        fsm = nodes[val].fsm
        assert (1, 1) in fsm.precommitted_rounds
        assert fsm.locked_block is not None  # Locked! (Rule 4)
        assert fsm.votes_cast[-1].phase == PHASE_PRECOMMIT
        assert fsm.votes_cast[-1].block_hash == fsm.locked_block

    # Deliver PRECOMMITs -> Triggers Finalization!
    router.deliver_all(sim_time=2.5)

    # --------------------------------------------------------------------------
    # Verification: Block finalized at Height 1, chain advances to Height 2!
    # --------------------------------------------------------------------------
    for val in ["node0", "node2", "node3"]:
        node = nodes[val]
        assert node.finalized_count == 1
        assert node.height == 2  # Chain successfully advanced to Height 2
        assert node.round == 0   # Round reset for new height
        assert node.locked_block is None  # Lock cleared for next height

        # Verify state transition took effect
        assert node.state.values.get("Alice/message") == "hello_tendermint"
