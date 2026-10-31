"""Validator node integration extension point for TV4.

Wires together the five module components:
- Cryptography (TV1)
- State & Ledger (TV2)
- Network Simulator (TV3)
- Consensus FSM (TV4)
- Locking, Byzantine, & Logger (TV5)
"""

from __future__ import annotations

from collections.abc import Sequence

from ..common.interfaces import (
    CryptoProtocol,
    LedgerProtocol,
    LockManagerProtocol,
    LoggerProtocol,
    NetworkProtocol,
    StateProtocol,
    VoteTrackerProtocol,
)
from ..common.mocks import (
    MockCrypto,
    MockLedger,
    MockLockManager,
    MockLogger,
    MockNetwork,
    MockState,
    MockVoteTracker,
)
from .fsm import ConsensusFSM


class ValidatorNode:
    """Full validator node coordinating consensus, network, state, and crypto."""

    def __init__(
        self,
        node_id: str,
        validators: Sequence[str],
        crypto: CryptoProtocol | None = None,
        state: StateProtocol | None = None,
        ledger: LedgerProtocol | None = None,
        network: NetworkProtocol | None = None,
        vote_tracker: VoteTrackerProtocol | None = None,
        lock_manager: LockManagerProtocol | None = None,
        logger: LoggerProtocol | None = None,
    ) -> None:
        self.node_id = node_id
        self.validators = list(validators)

        # Wire dependencies, defaulting to verified mocks during independent development
        self.crypto = crypto if crypto is not None else MockCrypto()
        self.state = state if state is not None else MockState()
        self.ledger = ledger if ledger is not None else MockLedger()
        self.network = network if network is not None else MockNetwork()
        self.vote_tracker = (
            vote_tracker
            if vote_tracker is not None
            else MockVoteTracker(validator_count=len(self.validators))
        )
        self.lock_manager = lock_manager if lock_manager is not None else MockLockManager()
        self.logger = logger if logger is not None else MockLogger()

        # Consensus FSM
        self.fsm = ConsensusFSM(node_id=self.node_id, validators=self.validators)

    def handle_packet(self, sim_time: float | int, packet_bytes: bytes) -> None:
        """Route packet to consensus state machine."""
        self.fsm.on_packet(sim_time, packet_bytes)

    def handle_timeout(
        self,
        sim_time: float | int,
        height: int,
        round: int,
        step: str,
    ) -> None:
        """Route timeout event to consensus state machine."""
        self.fsm.on_timeout(sim_time, height, round, step)


__all__ = ["ValidatorNode"]
