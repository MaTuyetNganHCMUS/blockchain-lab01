"""Validator node integration implementation for TV4.

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
    Transaction,
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
        chain_id: str = "lab01",
        crypto: CryptoProtocol | None = None,
        state: StateProtocol | None = None,
        ledger: LedgerProtocol | None = None,
        network: NetworkProtocol | None = None,
        vote_tracker: VoteTrackerProtocol | None = None,
        lock_manager: LockManagerProtocol | None = None,
        logger: LoggerProtocol | None = None,
        priv_key: bytes | None = None,
        pub_keys: dict[str, bytes] | None = None,
        timeout_propose: float = 2.0,
        timeout_prevote: float = 1.0,
        timeout_precommit: float = 1.0,
    ) -> None:
        self.node_id = node_id
        self.validators = list(validators)
        self.chain_id = chain_id

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
        self.fsm = ConsensusFSM(
            node_id=self.node_id,
            validators=self.validators,
            chain_id=self.chain_id,
            crypto=self.crypto,
            state=self.state,
            ledger=self.ledger,
            network=self.network,
            vote_tracker=self.vote_tracker,
            lock_manager=self.lock_manager,
            logger=self.logger,
            priv_key=priv_key,
            pub_keys=pub_keys,
            timeout_propose=timeout_propose,
            timeout_prevote=timeout_prevote,
            timeout_precommit=timeout_precommit,
        )

    # ==========================================================================
    # PROPERTIES & GETTERS
    # ==========================================================================

    @property
    def height(self) -> int:
        """Current consensus height."""
        return self.fsm.height

    @property
    def round(self) -> int:
        """Current consensus round."""
        return self.fsm.round

    @property
    def step(self) -> str:
        """Current FSM step (NEW_ROUND, PROPOSE, PREVOTE, PRECOMMIT, COMMIT)."""
        return self.fsm.step

    @property
    def locked_block(self) -> bytes | None:
        """Hash of currently locked block (Rule 4), or None."""
        return self.fsm.locked_block

    @property
    def locked_round(self) -> int:
        """Round at which the current lock was acquired."""
        return self.fsm.locked_round

    @property
    def is_validator(self) -> bool:
        """Whether this node belongs to the active validator set."""
        return self.node_id in self.validators

    @property
    def finalized_count(self) -> int:
        """Count of blocks finalized by this node."""
        return len(self.fsm.finalized_blocks)

    # ==========================================================================
    # LIFECYCLE & EVENT HANDLING
    # ==========================================================================

    def start(self, sim_time: float | int = 0.0) -> None:
        """Initialize the validator node at height 1, round 0."""
        self.fsm.start_height(sim_time, 1)

    def submit_transaction(self, tx: Transaction) -> bool:
        """Ingest a transaction into node's local mempool."""
        self.fsm.mempool.append(tx)
        return True

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
