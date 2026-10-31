"""Validator consensus state machine extension point for TV4.

Implements the BFT Consensus Finite State Machine:
- Phases: NEW_ROUND -> PROPOSE -> PREVOTE -> PRECOMMIT -> COMMIT
- Deterministic proposer selection: proposer = validators[(height + round) % n]
- Timeout handling and round changes
"""

from __future__ import annotations

from collections.abc import Sequence

from ..common.interfaces import (
    ConsensusProtocol,
    STEP_COMMIT,
    STEP_NEW_ROUND,
    STEP_PRECOMMIT,
    STEP_PREVOTE,
    STEP_PROPOSE,
    select_proposer,
)


class ConsensusFSM(ConsensusProtocol):
    """BFT consensus finite state machine conforming to ConsensusProtocol."""

    def __init__(
        self,
        node_id: str,
        validators: Sequence[str],
        timeout_propose: float = 2.0,
        timeout_prevote: float = 1.0,
        timeout_precommit: float = 1.0,
    ) -> None:
        self.node_id = node_id
        self.validators = list(validators)
        self.timeout_propose = timeout_propose
        self.timeout_prevote = timeout_prevote
        self.timeout_precommit = timeout_precommit

        # FSM State variables
        self.height: int = 1
        self.round: int = 0
        self.step: str = STEP_NEW_ROUND
        self.locked_block: bytes | None = None
        self.locked_round: int = -1
        self.valid_block: bytes | None = None
        self.valid_round: int = -1

    def get_proposer(self, height: int, round: int) -> str:
        """Calculate the designated block proposer for (height, round)."""
        return select_proposer(height, round, self.validators)

    def is_current_proposer(self) -> bool:
        """Check if this node is the designated proposer for current (height, round)."""
        return self.get_proposer(self.height, self.round) == self.node_id

    def on_packet(self, sim_time: float | int, packet_bytes: bytes) -> None:
        """Process an incoming network packet according to current FSM state."""
        # Core FSM packet dispatch to be expanded during feature implementation
        pass

    def on_timeout(
        self,
        sim_time: float | int,
        height: int,
        round: int,
        step: str,
    ) -> None:
        """Handle timeout expiration, triggering vote NIL or round advance."""
        if height != self.height or round != self.round:
            return

        if step == STEP_PROPOSE:
            # Propose timeout: advance to prevote NIL
            self.step = STEP_PREVOTE
        elif step in (STEP_PREVOTE, STEP_PRECOMMIT):
            # Advance to next round with timeout backoff
            self.round += 1
            self.step = STEP_NEW_ROUND


__all__ = ["ConsensusFSM"]
