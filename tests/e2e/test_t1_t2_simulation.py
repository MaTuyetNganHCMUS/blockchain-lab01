"""End-to-End Simulation Tests for TV2 (Execution & Ledger View).
Implements Test T1 (8+ Node State Convergence) and Test T2 (Duplicate/Reordered Packet Resilience).
Owner: TV2
"""
from __future__ import annotations

import unittest

from src.common.interfaces import BlockBody, BlockHeader, Transaction
from src.execution import (
    Ledger,
    StateEngine,
    compute_header_hash,
    compute_tx_hash,
    compute_tx_root,
)


class SimulatedNode:
    """Mock Node representing local View, State, and Ledger for E2E testing."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.ledger = Ledger(with_genesis=True)
        self.state = StateEngine()

    def receive_and_commit_block(self, header: BlockHeader, body: BlockBody) -> bool:
        """Process incoming block: validate integrity and commit to local state and ledger."""
        is_valid, _ = self.ledger.validate_block(header, body, self.state)
        if not is_valid:
            return False

        self.state.commit_block(header, body)
        self.ledger.add_finalized_block(header, body)
        return True


class TestE2EScenarios(unittest.TestCase):
    """End-to-End Test Suite for T1 and T2 scenarios."""

    def test_t1_state_convergence_8_nodes(self) -> None:
        """Test T1: Normal execution with >= 8 nodes converging to identical blockchain and state."""
        num_nodes = 8
        nodes = [SimulatedNode(f"validator-{i}") for i in range(num_nodes)]

        # Sequence of 5 blocks with transactions from 4 distinct accounts
        proposers = ["validator-0", "validator-1", "validator-2", "validator-3", "validator-0"]
        nonces = {"Alice": 0, "Bob": 0, "Charlie": 0, "David": 0}

        # Reference authority node to produce consensus blocks
        authority_state = StateEngine()
        authority_ledger = Ledger(with_genesis=True)

        for height in range(1, 6):
            proposer = proposers[height - 1]
            block_txs: list[Transaction] = []

            # 4 transactions per block (1 per user)
            for user in ["Alice", "Bob", "Charlie", "David"]:
                nonces[user] += 1
                tx = Transaction(
                    sender=user,
                    key=f"{user}/balance",
                    value=str(100 * height + nonces[user]),
                    nonce=nonces[user],
                )
                block_txs.append(tx)

            # Authority computes valid state transition
            valid_run, expected_state_hash = authority_state.validate_and_compute_hash(block_txs)
            self.assertTrue(valid_run)

            tx_root = compute_tx_root([compute_tx_hash(tx) for tx in block_txs])
            parent_hash = authority_ledger.get_latest_hash()

            header = BlockHeader(
                height=height,
                round=0,
                parent_hash=parent_hash,
                state_hash=expected_state_hash,
                tx_root=tx_root,
                proposer=proposer,
            )
            body = BlockBody(header_hash=compute_header_hash(header), transactions=block_txs)

            # Authority commits block
            authority_state.commit_block(header, body)
            authority_ledger.add_finalized_block(header, body)

            # Broadcast and commit block to all 8 nodes
            for node in nodes:
                committed = node.receive_and_commit_block(header, body)
                self.assertTrue(
                    committed,
                    msg=f"Node {node.node_id} failed to commit block at height {height}",
                )

        # Verification across all 8 nodes
        ref_state_hash = nodes[0].state.get_state_hash()
        ref_chain_history = [compute_header_hash(h) for h, _ in nodes[0].ledger.get_chain_history()]

        self.assertEqual(len(ref_chain_history), 6)  # Genesis + 5 blocks

        for i, node in enumerate(nodes[1:], start=1):
            # Check 1: Chain height
            self.assertEqual(node.ledger.height, 5, msg=f"Node {i} height mismatch")

            # Check 2: Block hashes match reference
            node_chain = [compute_header_hash(h) for h, _ in node.ledger.get_chain_history()]
            self.assertEqual(
                node_chain,
                ref_chain_history,
                msg=f"Node {i} finalized chain hash mismatch",
            )

            # Check 3: State hash matches reference exactly
            self.assertEqual(
                node.state.get_state_hash(),
                ref_state_hash,
                msg=f"Node {i} state hash divergence",
            )

            # Check 4: Values are correct
            self.assertEqual(node.state.get("Alice/balance"), "505")
            self.assertEqual(node.state.get("David/balance"), "505")

    def test_t2_duplicate_and_reordered_packets_protection(self) -> None:
        """Test T2: Duplicate and reordered packets do not execute twice or corrupt state."""
        node = SimulatedNode("target-node")

        # Step 1: Commit Block 1 normally
        tx1 = Transaction(sender="Alice", key="Alice/data", value="initial", nonce=1)
        valid, s1_hash = node.state.validate_and_compute_hash([tx1])
        self.assertTrue(valid)

        header1 = BlockHeader(
            height=1,
            round=0,
            parent_hash=node.ledger.get_latest_hash(),
            state_hash=s1_hash,
            tx_root=compute_tx_root([compute_tx_hash(tx1)]),
            proposer="Validator-0",
        )
        body1 = BlockBody(header_hash=compute_header_hash(header1), transactions=[tx1])
        self.assertTrue(node.receive_and_commit_block(header1, body1))

        # Simulation: Network delivers duplicate Block 1
        # Node must reject duplicate block execution (parent hash mismatch or height mismatch)
        is_recommit_allowed, _ = node.ledger.validate_block(header1, body1, node.state)
        self.assertFalse(
            is_recommit_allowed,
            msg="Re-executing an already committed block must be rejected",
        )

        # Simulation: Network delivers duplicate transactions directly to StateEngine
        # Alice tries to execute tx1 again
        self.assertFalse(
            node.state.apply_transaction(tx1),
            msg="Duplicate transaction execution must be rejected",
        )

        # Simulation: Out-of-order transaction delivery (nonce 3 arrives before nonce 2)
        tx_nonce3 = Transaction(sender="Alice", key="Alice/data", value="future", nonce=3)
        self.assertFalse(
            node.state.apply_transaction(tx_nonce3),
            msg="Out-of-order transaction must be rejected",
        )

        # Legitimate sequential nonce 2 arrives -> accepted
        tx_nonce2 = Transaction(sender="Alice", key="Alice/data", value="updated", nonce=2)
        self.assertTrue(node.state.apply_transaction(tx_nonce2))
        self.assertEqual(node.state.get("Alice/data"), "updated")
        self.assertEqual(node.state.get_nonce("Alice"), 2)


if __name__ == "__main__":
    unittest.main()
