"""Validator consensus state machine implementation for TV4.

Implements Tendermint-style BFT Consensus FSM:
- Lifecycle: NEW_ROUND -> PROPOSE -> PREVOTE -> PRECOMMIT -> COMMIT
- Deterministic proposer selection: proposer = validators[(height + round) % n]
- Locking rules (Rule 1..7):
  * Rule 1: at most 1 PREVOTE per (H, R)
  * Rule 2: at most 1 PRECOMMIT per (H, R)
  * Rule 3: accept & prevote only valid proposal (parent link, signature, state hash)
  * Rule 4: lock block & record round on 2f + 1 PREVOTE for non-NIL block
  * Rule 5: prevote different block only with higher-round Polka (Proof-of-Lock-Change)
  * Rule 6: domain separation and height/round validity
  * Rule 7: count at most 1 vote per validator per (H, R, phase)
- Liveness: progressive timeout backoff, automatic vote NIL on propose/prevote timeout
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..common.interfaces import (
    BlockBody,
    BlockHeader,
    ConsensusProtocol,
    CryptoProtocol,
    DOMAIN_HEADER,
    DOMAIN_VOTE,
    EVENT_FINALIZE,
    EVENT_LOCK,
    EVENT_PRECOMMIT,
    EVENT_PREVOTE,
    EVENT_PROPOSE,
    GENESIS_PARENT_HASH,
    LedgerProtocol,
    LockManagerProtocol,
    LoggerProtocol,
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    MSG_TX,
    MSG_VOTE,
    NetworkProtocol,
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    STEP_COMMIT,
    STEP_NEW_ROUND,
    STEP_PRECOMMIT,
    STEP_PREVOTE,
    STEP_PROPOSE,
    StateProtocol,
    Transaction,
    Vote,
    VoteTrackerProtocol,
    select_proposer,
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
from .codec import pack_message, unpack_message


class ConsensusFSM(ConsensusProtocol):
    """BFT consensus finite state machine conforming to ConsensusProtocol."""

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
        timeout_delta: float = 0.5,
    ) -> None:
        self.node_id = node_id
        self.validators = list(validators)
        self.chain_id = chain_id

        # Dependency services (defaults to verified mocks)
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

        # Keys
        if priv_key is not None:
            self.priv_key = priv_key
        elif isinstance(self.crypto, MockCrypto):
            p_priv, p_pub = self.crypto.generate_keypair(self.node_id)
            self.priv_key = p_priv
            if pub_keys is None:
                pub_keys = {self.node_id: p_pub}
        elif hasattr(self.crypto, "generate_keypair"):
            p_priv, p_pub = self.crypto.generate_keypair()
            self.priv_key = p_priv
            if pub_keys is None:
                pub_keys = {self.node_id: p_pub}
        else:
            self.priv_key = b"default_priv_key_" + self.node_id.encode("utf-8")

        self.pub_keys: dict[str, bytes] = dict(pub_keys) if pub_keys else {}

        # Timeout configuration
        self.timeout_propose = timeout_propose
        self.timeout_prevote = timeout_prevote
        self.timeout_precommit = timeout_precommit
        self.timeout_delta = timeout_delta

        # Consensus FSM State variables
        self.height: int = 1
        self.round: int = 0
        self.step: str = STEP_NEW_ROUND

        # Locking State (Rules 4, 5)
        self.locked_block: bytes | None = None
        self.locked_round: int = -1
        self.valid_block: bytes | None = None
        self.valid_round: int = -1

        # Execution tracking & mempool
        self.mempool: list[Transaction] = []
        self.prevoted_rounds: set[tuple[int, int]] = set()      # Rule 1
        self.precommitted_rounds: set[tuple[int, int]] = set()  # Rule 2
        self.round_advanced: set[tuple[int, int]] = set()
        self.finalized_heights: set[int] = set()
        self.proposals: dict[tuple[int, int], tuple[BlockHeader, BlockBody | None]] = {}
        self.block_cache: dict[bytes, tuple[BlockHeader, BlockBody]] = {}
        self.votes_cast: list[Vote] = []
        self.finalized_blocks: list[tuple[BlockHeader, BlockBody]] = []

    # ==========================================================================
    # PROPOSER & TIMEOUT HELPERS
    # ==========================================================================

    def get_proposer(self, height: int, round: int) -> str:
        r"""Compute designated proposer: $p = \text{validators}[(H + R) \pmod n]$."""
        return select_proposer(height, round, self.validators)

    def is_current_proposer(self) -> bool:
        """Check if this node is the designated proposer for (current_height, current_round)."""
        return self.get_proposer(self.height, self.round) == self.node_id

    def get_timeout_delay(self, base: float, round: int) -> float:
        """Compute progressive timeout backoff for round liveness."""
        return base + (round * self.timeout_delta)

    # ==========================================================================
    # INTEGRATION ADAPTER HELPERS (TV1 / TV2 / TV5)
    # ==========================================================================

    def _safe_log(
        self,
        sim_time: float | int,
        event_type: str,
        detail: str,
    ) -> None:
        """Log consensus events while respecting strict logger schemas (TV5)."""
        if event_type == "NEW_ROUND" and getattr(self.logger, "strict_event_types", False):
            return
        try:
            self.logger.log(sim_time, self.node_id, event_type, self.height, self.round, detail)
        except ValueError:
            if event_type != "NEW_ROUND":
                raise

    def _expected_parent_hash(self) -> bytes:
        """Resolve expected parent hash compatible with MockLedger and TV2 Ledger."""
        if hasattr(self.ledger, "get_latest_hash"):
            return self.ledger.get_latest_hash()
        if self.finalized_blocks:
            return self.crypto.hash_bytes(
                self.crypto.encode_canonical(self.finalized_blocks[-1][0])
            )
        return GENESIS_PARENT_HASH

    def _compute_tx_root(self, txs: tuple[Transaction, ...]) -> bytes:
        """Compute tx_root using TV2 commitment helper when TV2 Ledger is wired."""
        if hasattr(self.ledger, "validate_block"):
            from ..execution.commitment import compute_tx_hash, compute_tx_root

            return compute_tx_root([compute_tx_hash(tx) for tx in txs])
        return self.crypto.hash_bytes(self.crypto.encode_canonical(txs))

    def _compute_header_hash(self, header: BlockHeader) -> bytes:
        """Compute canonical block header hash aligned with wired ledger."""
        if hasattr(self.ledger, "validate_block"):
            from ..execution.commitment import compute_header_hash

            return compute_header_hash(header)
        return self.crypto.hash_bytes(self.crypto.encode_canonical(header))

    def _select_valid_mempool_txs(self) -> tuple[tuple[Transaction, ...], bytes]:
        """Select valid mempool transactions and compute resulting candidate state hash."""
        candidate = tuple(self.mempool)
        ok, state_hash = self.state.validate_and_compute_hash(candidate)
        if ok:
            return candidate, state_hash

        accepted: list[Transaction] = []
        for tx in candidate:
            trial = tuple(accepted + [tx])
            tx_ok, _ = self.state.validate_and_compute_hash(trial)
            if tx_ok:
                accepted.append(tx)
        final_txs = tuple(accepted)
        _, final_hash = self.state.validate_and_compute_hash(final_txs)
        return final_txs, final_hash

    def _vote_signing_payload(self, unsigned_vote: Vote) -> bytes:
        """Compute signing payload aligned with MockVoteTracker or TV5 VoteTracker."""
        if hasattr(self.vote_tracker, "vote_domain"):
            from ..security.locking import vote_signing_bytes

            return vote_signing_bytes(unsigned_vote)
        return self.crypto.encode_canonical(unsigned_vote)

    # ==========================================================================
    # LIFECYCLE: START HEIGHT & ROUND
    # ==========================================================================

    def start_height(self, sim_time: float | int, height: int) -> None:
        """Begin consensus on a new block height."""
        self.height = height
        self.round = 0
        self.locked_block = None
        self.locked_round = -1
        self.valid_block = None
        self.valid_round = -1
        self.round_advanced.clear()
        if hasattr(self.vote_tracker, "set_expected_height"):
            self.vote_tracker.set_expected_height(height)
        if hasattr(self.lock_manager, "start_height"):
            self.lock_manager.start_height(height)
        elif hasattr(self.lock_manager, "unlock"):
            self.lock_manager.unlock()
        self.start_round(sim_time, 0)

    def _schedule_step_timeout(self, step: str, base_delay: float) -> None:
        """Schedule discrete-event timeout using TV3 'H:R:STEP' tag convention."""
        delay = self.get_timeout_delay(base_delay, self.round)
        self.network.schedule_timeout(
            self.node_id,
            delay,
            f"{self.height}:{self.round}:{step}",
        )

    def start_round(self, sim_time: float | int, round: int) -> None:
        """Initialize or advance to round R at current height H."""
        self.round = round
        self.step = STEP_NEW_ROUND

        proposer = self.get_proposer(self.height, self.round)
        self._safe_log(sim_time, "NEW_ROUND", f"proposer={proposer}")

        # Schedule propose timeout
        self._schedule_step_timeout(STEP_PROPOSE, self.timeout_propose)

        if self.is_current_proposer():
            # Designated proposer proposes a block
            self.step = STEP_PROPOSE
            self._propose_block(sim_time)
        else:
            self.step = STEP_PROPOSE

    # ==========================================================================
    # PROPOSAL CREATION & BROADCAST
    # ==========================================================================

    def _propose_block(self, sim_time: float | int) -> None:
        """Create, sign, and broadcast proposal block."""
        parent_hash = self._expected_parent_hash()

        if self.locked_block is not None and self.locked_block in self.block_cache:
            # Re-propose locked block
            cached_hdr, cached_body = self.block_cache[self.locked_block]
            header = BlockHeader(
                height=self.height,
                round=self.round,
                parent_hash=cached_hdr.parent_hash,
                state_hash=cached_hdr.state_hash,
                tx_root=cached_hdr.tx_root,
                proposer=self.node_id,
            )
            txs = cached_body.transactions
        else:
            # Propose valid transactions from mempool
            txs, state_hash = self._select_valid_mempool_txs()
            tx_root = self._compute_tx_root(txs)
            header = BlockHeader(
                height=self.height,
                round=self.round,
                parent_hash=parent_hash,
                state_hash=state_hash,
                tx_root=tx_root,
                proposer=self.node_id,
            )

        # Sign header under HEADER:<chain_id> domain
        hdr_canonical = self.crypto.encode_canonical(header)
        hdr_sig = self.crypto.sign(
            self.priv_key,
            f"{DOMAIN_HEADER}:{self.chain_id}",
            hdr_canonical,
        )
        signed_header = BlockHeader(
            height=header.height,
            round=header.round,
            parent_hash=header.parent_hash,
            state_hash=header.state_hash,
            tx_root=header.tx_root,
            proposer=header.proposer,
            sig=hdr_sig,
        )

        header_hash = self._compute_header_hash(signed_header)
        body = BlockBody(header_hash=header_hash, transactions=txs)

        # Cache locally
        self.block_cache[header_hash] = (signed_header, body)
        self.proposals[(self.height, self.round)] = (signed_header, body)

        self.logger.log(
            sim_time,
            self.node_id,
            EVENT_PROPOSE,
            self.height,
            self.round,
            f"hash={header_hash.hex()[:16]} txs={len(txs)}",
        )

        # Broadcast Header and Body
        packed_hdr = pack_message(MSG_HEADER, signed_header)
        self.network.broadcast_header(self.node_id, packed_hdr)

        packed_body = pack_message(MSG_BODY, body)
        for peer in self.validators:
            if peer != self.node_id:
                self.network.send(self.node_id, peer, packed_body)

        # Locally evaluate own proposal
        self._evaluate_proposal_and_prevote(sim_time, signed_header, body)

    # ==========================================================================
    # PROPOSAL HANDLING & PREVOTING
    # ==========================================================================

    def on_proposal_header(self, sim_time: float | int, header: BlockHeader) -> None:
        """Process incoming proposed BlockHeader."""
        if header.height != self.height or header.round != self.round:
            return

        expected_proposer = self.get_proposer(self.height, self.round)
        if header.proposer != expected_proposer:
            # Reject proposal from non-designated proposer
            return

        # Check signature under HEADER:<chain_id>
        pub_key = self.pub_keys.get(header.proposer)
        if pub_key is not None:
            # Reconstruct unsigned header for canonical signature verification
            raw_hdr = BlockHeader(
                height=header.height,
                round=header.round,
                parent_hash=header.parent_hash,
                state_hash=header.state_hash,
                tx_root=header.tx_root,
                proposer=header.proposer,
            )
            canonical_bytes = self.crypto.encode_canonical(raw_hdr)
            if not self.crypto.verify(
                pub_key,
                f"{DOMAIN_HEADER}:{self.chain_id}",
                canonical_bytes,
                header.sig,
            ):
                return

        existing = self.proposals.get((header.height, header.round))
        body = existing[1] if existing else None
        self.proposals[(header.height, header.round)] = (header, body)

        # If body is already present or empty, evaluate prevote
        if body is not None:
            self._evaluate_proposal_and_prevote(sim_time, header, body)
        else:
            # Request block body from proposer
            hdr_hash = self._compute_header_hash(header)
            req_packet = pack_message(MSG_BODY_REQUEST, hdr_hash)
            self.network.send(self.node_id, header.proposer, req_packet)

    def on_proposal_body(self, sim_time: float | int, body: BlockBody) -> None:
        """Process incoming BlockBody."""
        existing = self.proposals.get((self.height, self.round))
        if existing and existing[0]:
            header = existing[0]
            header_hash = self._compute_header_hash(header)
            if body.header_hash == header_hash:
                self.proposals[(self.height, self.round)] = (header, body)
                self.block_cache[header_hash] = (header, body)
                self._evaluate_proposal_and_prevote(sim_time, header, body)

    def _evaluate_proposal_and_prevote(
        self,
        sim_time: float | int,
        header: BlockHeader,
        body: BlockBody | None,
    ) -> None:
        """Validate proposal according to Rule 3, 4, 5 and cast PREVOTE."""
        if (self.height, self.round) in self.prevoted_rounds:
            # Rule 1: already prevoted for this round
            return

        header_hash = self._compute_header_hash(header)
        if body is not None:
            self.block_cache[header_hash] = (header, body)

        # 1 & 2. Validate Block (TV2 full ledger validation when available)
        if body is not None and hasattr(self.ledger, "validate_block"):
            is_valid_block, _ = self.ledger.validate_block(header, body, self.state)
            if not is_valid_block:
                self.step = STEP_PREVOTE
                self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                self._schedule_prevote_timeout()
                return
        else:
            expected_parent = self._expected_parent_hash()
            if header.parent_hash != expected_parent:
                self.step = STEP_PREVOTE
                self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                self._schedule_prevote_timeout()
                return

            if body is not None:
                valid_txs, computed_state_hash = self.state.validate_and_compute_hash(
                    body.transactions
                )
                if not valid_txs or computed_state_hash != header.state_hash:
                    self.step = STEP_PREVOTE
                    self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                    self._schedule_prevote_timeout()
                    return

        # 3. Check Locking Rules (Rule 4, 5)
        can_prevote = self.lock_manager.can_prevote(
            header_hash,
            self.round,
            polka_round=self.valid_round,
        )

        vote_hash = header_hash if can_prevote else NIL_BLOCK_HASH
        self.step = STEP_PREVOTE
        self.cast_vote(sim_time, PHASE_PREVOTE, vote_hash)
        self._schedule_prevote_timeout()

    def _schedule_prevote_timeout(self) -> None:
        self._schedule_step_timeout(STEP_PREVOTE, self.timeout_prevote)

    # ==========================================================================
    # VOTE CASTING & VOTE HANDLING
    # ==========================================================================

    def cast_vote(self, sim_time: float | int, phase: str, block_hash: bytes) -> Vote:
        """Create, sign, record, and broadcast a PREVOTE or PRECOMMIT."""
        if phase == PHASE_PREVOTE:
            self.prevoted_rounds.add((self.height, self.round))  # Rule 1
            event_type = EVENT_PREVOTE
        else:
            self.precommitted_rounds.add((self.height, self.round))  # Rule 2
            event_type = EVENT_PRECOMMIT

        # Sign under VOTE:<chain_id> domain
        unsigned_vote = Vote(
            height=self.height,
            round=self.round,
            phase=phase,
            block_hash=block_hash,
            voter=self.node_id,
        )
        vote_payload = self._vote_signing_payload(unsigned_vote)
        sig = self.crypto.sign(
            self.priv_key,
            f"{DOMAIN_VOTE}:{self.chain_id}",
            vote_payload,
        )
        signed_vote = Vote(
            height=self.height,
            round=self.round,
            phase=phase,
            block_hash=block_hash,
            voter=self.node_id,
            sig=sig,
        )

        if hasattr(self.lock_manager, "record_local_vote"):
            self.lock_manager.record_local_vote(signed_vote)

        self.votes_cast.append(signed_vote)
        self._safe_log(
            sim_time,
            event_type,
            f"hash={block_hash.hex()[:16] if block_hash != NIL_BLOCK_HASH else 'NIL'}",
        )

        # Broadcast vote to all validators
        packed_vote = pack_message(MSG_VOTE, signed_vote)
        for peer in self.validators:
            if peer != self.node_id:
                self.network.send(self.node_id, peer, packed_vote)

        # Feed to local tracker and evaluate immediate quorum completion
        quorum_res = self.vote_tracker.add_vote(signed_vote)
        self._handle_quorum_result(sim_time, phase, quorum_res)
        return signed_vote

    def on_vote(self, sim_time: float | int, vote: Vote) -> None:
        """Process incoming Vote packet."""
        if vote.height != self.height or vote.round != self.round:
            return

        # Check signature if public key is known and signature is provided
        pub_key = self.pub_keys.get(vote.voter)
        if pub_key is not None and vote.sig:
            unsigned = Vote(
                height=vote.height,
                round=vote.round,
                phase=vote.phase,
                block_hash=vote.block_hash,
                voter=vote.voter,
            )
            vote_payload = self._vote_signing_payload(unsigned)
            if not self.crypto.verify(
                pub_key,
                f"{DOMAIN_VOTE}:{self.chain_id}",
                vote_payload,
                vote.sig,
            ):
                return

        quorum_res = self.vote_tracker.add_vote(vote)
        self._handle_quorum_result(sim_time, vote.phase, quorum_res)

    def _handle_quorum_result(self, sim_time: float | int, phase: str, quorum_res: Any) -> None:
        """Process quorum transition after a PREVOTE or PRECOMMIT vote."""
        if not quorum_res.has_quorum or quorum_res.round != self.round:
            return

        if phase == PHASE_PREVOTE:
            if (self.height, self.round) not in self.precommitted_rounds:
                if hasattr(self.lock_manager, "observe_quorum"):
                    self.lock_manager.observe_quorum(quorum_res)
                if quorum_res.block_hash != NIL_BLOCK_HASH:
                    # Polka on block B -> Lock on B (Rule 4)
                    self.locked_block = quorum_res.block_hash
                    self.locked_round = self.round
                    self.valid_block = quorum_res.block_hash
                    self.valid_round = self.round
                    if not hasattr(self.lock_manager, "observe_quorum"):
                        self.lock_manager.record_lock(quorum_res.block_hash, self.round)

                    self._safe_log(
                        sim_time,
                        EVENT_LOCK,
                        f"hash={quorum_res.block_hash.hex()[:16]}",
                    )
                    self.step = STEP_PRECOMMIT
                    self.cast_vote(sim_time, PHASE_PRECOMMIT, quorum_res.block_hash)
                else:
                    # Quorum on NIL -> Precommit NIL
                    self.step = STEP_PRECOMMIT
                    self.cast_vote(sim_time, PHASE_PRECOMMIT, NIL_BLOCK_HASH)

                if self.step == STEP_PRECOMMIT:
                    self._schedule_step_timeout(STEP_PRECOMMIT, self.timeout_precommit)

        elif phase == PHASE_PRECOMMIT:
            if quorum_res.block_hash != NIL_BLOCK_HASH:
                # Finalize block!
                self._finalize_block(sim_time, quorum_res.block_hash)
            else:
                # Quorum of Precommit NIL -> Advance round once
                if (self.height, self.round) not in self.round_advanced:
                    self.round_advanced.add((self.height, self.round))
                    self.start_round(sim_time, self.round + 1)

    def _finalize_block(self, sim_time: float | int, block_hash: bytes) -> None:
        """Commit finalized block to state and ledger, then advance height."""
        if self.step == STEP_COMMIT or self.height in self.finalized_heights:
            return
        self.step = STEP_COMMIT
        self.finalized_heights.add(self.height)

        cached = self.block_cache.get(block_hash)
        if not cached:
            # Fallback construct placeholder if body was delivered out of band
            cached_hdr = BlockHeader(
                height=self.height,
                round=self.round,
                parent_hash=self._expected_parent_hash(),
                state_hash=b"",
                tx_root=b"",
                proposer=self.get_proposer(self.height, self.round),
            )
            cached_body = BlockBody(header_hash=block_hash, transactions=())
            cached = (cached_hdr, cached_body)

        header, body = cached
        self.state.commit_block(body)
        self.ledger.add_finalized_block(header, body)
        self.finalized_blocks.append((header, body))

        self._safe_log(
            sim_time,
            EVENT_FINALIZE,
            f"hash={block_hash.hex()[:16]} height={self.height}",
        )

        # Clear executed transactions from mempool
        executed_nonces = {(tx.sender, tx.nonce) for tx in body.transactions}
        self.mempool = [tx for tx in self.mempool if (tx.sender, tx.nonce) not in executed_nonces]

        # Advance to next block height
        self.start_height(sim_time, self.height + 1)

    # ==========================================================================
    # TIMEOUT HANDLERS (LIVENESS & ROUND ADVANCE)
    # ==========================================================================

    def on_timeout(
        self,
        sim_time: float | int,
        height: int,
        round: int,
        step: str,
    ) -> None:
        """Route timeout event triggered by network simulator."""
        if height != self.height or round != self.round:
            return

        if step == STEP_PROPOSE:
            self.on_timeout_propose(sim_time, height, round)
        elif step == STEP_PREVOTE:
            self.on_timeout_prevote(sim_time, height, round)
        elif step == STEP_PRECOMMIT:
            self.on_timeout_precommit(sim_time, height, round)

    def on_timeout_propose(self, sim_time: float | int, height: int, round: int) -> None:
        """Handle proposal timeout (proposer crashed, silent, or slow)."""
        if height != self.height or round != self.round:
            return
        if (height, round) in self.prevoted_rounds:
            return

        # Proposer failed to propose in time: prevote NIL
        self.step = STEP_PREVOTE
        self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
        if self.step == STEP_PREVOTE:
            self._schedule_prevote_timeout()

    def on_timeout_prevote(self, sim_time: float | int, height: int, round: int) -> None:
        """Handle prevote timeout (quorum of prevotes not reached)."""
        if height != self.height or round != self.round:
            return
        if (height, round) in self.precommitted_rounds:
            return

        # Prevote timeout reached: precommit NIL
        self.step = STEP_PRECOMMIT
        self.cast_vote(sim_time, PHASE_PRECOMMIT, NIL_BLOCK_HASH)
        if self.step == STEP_PRECOMMIT:
            self._schedule_step_timeout(STEP_PRECOMMIT, self.timeout_precommit)

    def on_timeout_precommit(self, sim_time: float | int, height: int, round: int) -> None:
        """Handle precommit timeout (block not finalized): advance to next round."""
        if height != self.height or round != self.round:
            return
        if self.step == STEP_COMMIT:
            return
        if (height, round) in self.round_advanced:
            return

        self.round_advanced.add((height, round))
        # Advance to round + 1
        self.start_round(sim_time, self.round + 1)

    # ==========================================================================
    # PACKET DISPATCHER
    # ==========================================================================

    def on_packet(self, sim_time: float | int, packet_bytes: bytes) -> None:
        """Process incoming wire packet."""
        try:
            msg_type, payload = unpack_message(packet_bytes)
        except Exception:
            return

        if msg_type == MSG_HEADER and isinstance(payload, BlockHeader):
            self.on_proposal_header(sim_time, payload)
        elif msg_type == MSG_BODY and isinstance(payload, BlockBody):
            self.on_proposal_body(sim_time, payload)
        elif msg_type == MSG_VOTE and isinstance(payload, Vote):
            self.on_vote(sim_time, payload)
        elif msg_type == MSG_TX and isinstance(payload, Transaction):
            self.mempool.append(payload)
        elif msg_type == MSG_BODY_REQUEST and isinstance(payload, bytes):
            # Reply with body if in block cache
            cached = self.block_cache.get(payload)
            if cached:
                reply = pack_message(MSG_BODY, cached[1])
                # Broadcast or send back to requestor
                for peer in self.validators:
                    if peer != self.node_id:
                        self.network.send(self.node_id, peer, reply)


__all__ = ["ConsensusFSM"]
