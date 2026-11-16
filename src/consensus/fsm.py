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
    DOMAIN_HEADER,
    DOMAIN_VOTE,
    EVENT_FINALIZE,
    EVENT_LOCK,
    EVENT_PRECOMMIT,
    EVENT_PREVOTE,
    EVENT_PROPOSE,
    EVENT_REJECT_BODY,
    EVENT_REJECT_HEADER,
    EVENT_REJECT_PACKET,
    EVENT_REJECT_TX,
    EVENT_REJECT_VOTE,
    GENESIS_PARENT_HASH,
    MSG_BODY,
    MSG_BODY_REQUEST,
    MSG_HEADER,
    MSG_HEADER_REQUEST,
    MSG_TX,
    MSG_VOTE,
    NIL_BLOCK_HASH,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
    REASON_EQUIVOCATION,
    REASON_INVALID_BLOCK,
    REASON_INVALID_SIGNATURE,
    REASON_MALFORMED_PACKET,
    REASON_OVERSIZED_BLOCK,
    REASON_UNACCEPTED_HEADER,
    REASON_UNKNOWN_SENDER,
    REASON_WRONG_HEIGHT,
    REASON_WRONG_PROPOSER,
    REASON_WRONG_ROUND,
    STEP_COMMIT,
    STEP_NEW_ROUND,
    STEP_PRECOMMIT,
    STEP_PREVOTE,
    STEP_PROPOSE,
    BlockBody,
    BlockHeader,
    BlockPolicy,
    ConsensusProtocol,
    CryptoProtocol,
    LedgerProtocol,
    LockManagerProtocol,
    LoggerProtocol,
    NetworkProtocol,
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
from ..execution.mempool import AdmissionResult, Mempool, MempoolConfig, body_wire_size
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
        mempool_config: MempoolConfig | None = None,
        block_policy: BlockPolicy | None = None,
        tx_gossip_fanout: int = 4,
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

        if not isinstance(self.vote_tracker, MockVoteTracker):
            from ..security.locking import validate_bft_validator_set

            _, required_quorum = validate_bft_validator_set(
                self.validators,
                getattr(self.vote_tracker, "quorum", None),
            )
            if not isinstance(self.lock_manager, MockLockManager):
                lock_quorum = getattr(self.lock_manager, "quorum_threshold", None)
                if lock_quorum is not None and lock_quorum < required_quorum:
                    raise ValueError(
                        f"LockManager quorum_threshold={lock_quorum} < required {required_quorum}"
                    )

        self.block_policy = block_policy if block_policy is not None else BlockPolicy()
        if hasattr(self.ledger, "block_policy") and block_policy is not None:
            self.ledger.block_policy = self.block_policy
        self.tx_gossip_fanout = max(1, tx_gossip_fanout)

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
        if hasattr(self.ledger, "set_public_keys"):
            self.ledger.set_public_keys(self.pub_keys)
        if hasattr(self.state, "set_public_keys"):
            self.state.set_public_keys(self.pub_keys)

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
        self.mempool = Mempool(config=mempool_config)
        self.seen_tx_hashes: set[bytes] = set()
        self.accepted_headers: set[bytes] = set()
        self.headers_by_hash: dict[bytes, BlockHeader] = {}
        self.header_sources: dict[bytes, str] = {}
        self.quorum_peers: dict[bytes, list[str]] = {}
        self.requested_headers: set[bytes] = set()
        self.requested_bodies: dict[bytes, int] = {}
        self.prevoted_rounds: set[tuple[int, int]] = set()      # Rule 1
        self.precommitted_rounds: set[tuple[int, int]] = set()  # Rule 2
        self.round_advanced: set[tuple[int, int]] = set()
        self.finalized_heights: set[int] = set()
        self.proposals: dict[tuple[int, int], tuple[BlockHeader, BlockBody | None]] = {}
        self.block_cache: dict[bytes, tuple[BlockHeader, BlockBody]] = {}
        self.votes_cast: list[Vote] = []
        self.finalized_blocks: list[tuple[BlockHeader, BlockBody]] = []
        self.pending_commits: dict[bytes, tuple[float | int, int, int]] = {}
        self.unlinked_bodies: dict[bytes, BlockBody] = {}
        self.future_headers: dict[int, list[BlockHeader]] = {}
        self.future_votes: dict[int, list[Vote]] = {}
        self.future_round_votes: dict[tuple[int, int], list[Vote]] = {}

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
        *,
        height: int | None = None,
        round: int | None = None,
    ) -> None:
        """Log consensus events while respecting strict logger schemas (TV5)."""
        if event_type == "NEW_ROUND" and getattr(self.logger, "strict_event_types", False):
            return
        log_h = self.height if height is None else max(0, height)
        log_r = self.round if round is None else max(0, round)
        try:
            self.logger.log(sim_time, self.node_id, event_type, log_h, log_r, detail)
        except ValueError:
            if event_type != "NEW_ROUND":
                raise

    def _log_reject(
        self,
        sim_time: float | int,
        event_type: str,
        reason_code: str,
        *,
        msg_type: str,
        detail_extra: str = "",
        height: int | None = None,
        round: int | None = None,
    ) -> None:
        """Emit structured deterministic rejection log entry for T3/auditability."""
        detail = f"reason={reason_code} msg={msg_type}"
        if detail_extra:
            detail = f"{detail} {detail_extra}"
        self._safe_log(sim_time, event_type, detail, height=height, round=round)

    # ==========================================================================
    # TRANSACTION ADMISSION & BOUNDED GOSSIP
    # ==========================================================================

    def admit_transaction(
        self,
        tx: Transaction,
        *,
        source: str = "local",
        sim_time: float | int = 0.0,
        sender: str = "",
    ) -> AdmissionResult:
        """Unified transaction admission path for local submissions and MSG_TX."""
        result = self.mempool.admit(tx, state=self.state, sim_time=float(sim_time))
        if not result.accepted:
            tx_tag = result.tx_hash.hex()[:16] if result.tx_hash else "none"
            self._log_reject(
                sim_time,
                EVENT_REJECT_TX,
                result.reason_code,
                msg_type=MSG_TX,
                detail_extra=f"source={source} tx={tx_tag}",
            )
            return result

        if result.tx_hash not in self.seen_tx_hashes:
            self.seen_tx_hashes.add(result.tx_hash)
            self._gossip_transaction(tx, result.tx_hash, exclude_peer=sender)
        return result

    def _gossip_transaction(
        self,
        tx: Transaction,
        tx_hash: bytes,
        *,
        exclude_peer: str = "",
    ) -> None:
        """Relay newly admitted transaction with bounded deterministic fan-out."""
        if not self.validators:
            return
        candidates: list[str] = []
        # Prioritize current and next proposers so TX reaches block builder in 1 hop.
        for r_offset in (0, 1):
            proposer = self.get_proposer(self.height, self.round + r_offset)
            if proposer not in (self.node_id, exclude_peer) and proposer not in candidates:
                candidates.append(proposer)

        n = len(self.validators)
        my_idx = self.validators.index(self.node_id) if self.node_id in self.validators else 0
        offset = int.from_bytes(tx_hash[:2], "big") % n if tx_hash else 0
        for step in range(1, n + 1):
            if len(candidates) >= self.tx_gossip_fanout:
                break
            peer = self.validators[(my_idx + offset + step) % n]
            if peer not in (self.node_id, exclude_peer) and peer not in candidates:
                candidates.append(peer)

        if not candidates:
            return
        packed_tx = pack_message(MSG_TX, tx)
        for peer in candidates:
            self.network.send(self.node_id, peer, packed_tx)

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
        """Select valid mempool transactions bounded by ``self.block_policy``."""
        return self.mempool.select_candidates(self.state, self.block_policy)

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
        self.pending_commits.clear()
        self.requested_headers.clear()
        self.requested_bodies.clear()
        self.future_round_votes = {
            k: v for k, v in self.future_round_votes.items() if k[0] >= height
        }
        if hasattr(self.vote_tracker, "set_expected_height"):
            self.vote_tracker.set_expected_height(height)
        if hasattr(self.lock_manager, "start_height"):
            self.lock_manager.start_height(height)
        elif hasattr(self.lock_manager, "unlock"):
            self.lock_manager.unlock()
        self.start_round(sim_time, 0)
        # Replay future headers and votes that arrived early for this height
        for hdr in self.future_headers.pop(height, []):
            self.on_proposal_header(sim_time, hdr)
        for vt in self.future_votes.pop(height, []):
            self.on_vote(sim_time, vt)

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
            existing = self.proposals.get((self.height, round))
            if existing is not None and existing[0] is not None:
                hdr, body = existing
                if body is not None:
                    self._evaluate_proposal_and_prevote(sim_time, hdr, body)
                else:
                    self._request_block_body(hdr, self._compute_header_hash(hdr))

        for buffered_vote in self.future_round_votes.pop((self.height, round), []):
            if self.height != buffered_vote.height or self.round != round:
                break
            self.on_vote(sim_time, buffered_vote)

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

        # Cache locally (Header-before-Body: only broadcast Header; peers pull Body via MSG_BODY_REQUEST)
        self.accepted_headers.add(header_hash)
        self.headers_by_hash[header_hash] = signed_header
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

        # Broadcast Header only (Phase 1 of Header-before-Body protocol)
        packed_hdr = pack_message(MSG_HEADER, signed_header)
        self.network.broadcast_header(self.node_id, packed_hdr)

        # Locally evaluate own proposal
        self._evaluate_proposal_and_prevote(sim_time, signed_header, body)

    # ==========================================================================
    # PROPOSAL HANDLING & PREVOTING
    # ==========================================================================

    def _schedule_body_retry(self, height: int, round_num: int) -> None:
        delay = min(0.5, max(0.05, self.timeout_propose / 2.0))
        self.network.schedule_timeout(
            self.node_id,
            delay,
            f"{height}:{round_num}:BODY_RETRY",
        )

    def _request_block_body(self, header: BlockHeader, header_hash: bytes) -> None:
        attempts = self.requested_bodies.get(header_hash, 0)
        if attempts >= 8:
            return
        self.requested_bodies[header_hash] = attempts + 1
        req_packet = pack_message(MSG_BODY_REQUEST, header_hash)
        target = header.proposer
        if attempts > 0:
            peers = [p for p in self.quorum_peers.get(header_hash, []) if p != self.node_id]
            alt = self.header_sources.get(header_hash)
            if alt and alt != self.node_id and alt not in peers:
                peers.insert(0, alt)
            if peers:
                target = peers[(attempts - 1) % len(peers)]
        if target and target != self.node_id:
            self.network.send(self.node_id, target, req_packet)
        if attempts == 0:
            self._schedule_body_retry(header.height, header.round)

    def on_proposal_header(
        self,
        sim_time: float | int,
        header: BlockHeader,
        sender: str = "",
    ) -> None:
        """Process incoming proposed BlockHeader."""
        if header.height > self.height:
            self.future_headers.setdefault(header.height, []).append(header)
            return
        if header.height < self.height:
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_WRONG_HEIGHT,
                msg_type=MSG_HEADER,
                detail_extra=f"got_h={header.height}",
            )
            return

        expected_proposer = self.get_proposer(header.height, header.round)
        if header.proposer != expected_proposer:
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_WRONG_PROPOSER,
                msg_type=MSG_HEADER,
                detail_extra=f"proposer={header.proposer} expected={expected_proposer}",
            )
            return

        # Check signature under HEADER:<chain_id>
        pub_key = self.pub_keys.get(header.proposer)
        if pub_key is None:
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_UNKNOWN_SENDER,
                msg_type=MSG_HEADER,
                detail_extra=f"proposer={header.proposer}",
            )
            return
        if not header.sig:
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_INVALID_SIGNATURE,
                msg_type=MSG_HEADER,
                detail_extra=f"proposer={header.proposer} empty_sig",
            )
            return

        from ..execution.commitment import header_signing_payload

        canonical_bytes = header_signing_payload(header)
        if not self.crypto.verify(
            pub_key,
            f"{DOMAIN_HEADER}:{self.chain_id}",
            canonical_bytes,
            header.sig,
        ):
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_INVALID_SIGNATURE,
                msg_type=MSG_HEADER,
                detail_extra=f"proposer={header.proposer}",
            )
            return

        header_hash = self._compute_header_hash(header)
        if header.round < self.round and header_hash not in self.pending_commits:
            self._log_reject(
                sim_time,
                EVENT_REJECT_HEADER,
                REASON_WRONG_ROUND,
                msg_type=MSG_HEADER,
                detail_extra=f"got_r={header.round}",
            )
            return

        self.accepted_headers.add(header_hash)
        self.headers_by_hash[header_hash] = header
        if sender and sender != self.node_id and sender in self.validators:
            self.header_sources[header_hash] = sender

        existing = self.proposals.get((header.height, header.round))
        body = existing[1] if (existing and existing[1] is not None) else None
        if body is None:
            cached_entry = self.block_cache.get(header_hash)
            if cached_entry and cached_entry[0] is not None and cached_entry[1] is not None:
                body = cached_entry[1]

        self.proposals[(header.height, header.round)] = (header, body)

        if body is not None:
            self.block_cache[header_hash] = (header, body)
            if header_hash in self.pending_commits:
                self._finalize_block(sim_time, header_hash)
            elif header.round == self.round:
                self._evaluate_proposal_and_prevote(sim_time, header, body)
        else:
            # Phase 2: Request block body only after header acceptance
            self._request_block_body(header, header_hash)

    def on_proposal_body(self, sim_time: float | int, body: BlockBody) -> None:
        """Process incoming BlockBody only if its matching header was already accepted."""
        if body.header_hash not in self.accepted_headers:
            self._log_reject(
                sim_time,
                EVENT_REJECT_BODY,
                REASON_UNACCEPTED_HEADER,
                msg_type=MSG_BODY,
                detail_extra=f"hash={body.header_hash.hex()[:16]}",
            )
            return

        header = self.headers_by_hash.get(body.header_hash)
        matched_round: int | None = header.round if header is not None else None
        if header is None:
            for (h, r), prop in self.proposals.items():
                if h == self.height and prop[0] and self._compute_header_hash(prop[0]) == body.header_hash:
                    matched_round = r
                    header = prop[0]
                    break

        if header is None or matched_round is None:
            self._log_reject(
                sim_time,
                EVENT_REJECT_BODY,
                REASON_UNACCEPTED_HEADER,
                msg_type=MSG_BODY,
                detail_extra=f"hash={body.header_hash.hex()[:16]}",
            )
            return

        if len(body.transactions) > self.block_policy.max_txs or (
            body_wire_size(body) > self.block_policy.max_bytes
        ):
            self._log_reject(
                sim_time,
                EVENT_REJECT_BODY,
                REASON_OVERSIZED_BLOCK,
                msg_type=MSG_BODY,
                detail_extra=f"hash={body.header_hash.hex()[:16]} txs={len(body.transactions)}",
            )
            if matched_round == self.round and (self.height, self.round) not in self.prevoted_rounds:
                self.step = STEP_PREVOTE
                self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                self._schedule_prevote_timeout()
            return

        header_hash = self._compute_header_hash(header)
        self.requested_bodies.pop(header_hash, None)
        self.proposals[(header.height, matched_round)] = (header, body)
        self.block_cache[header_hash] = (header, body)
        if header_hash in self.pending_commits:
            self._finalize_block(sim_time, header_hash)
        elif matched_round == self.round:
            self._evaluate_proposal_and_prevote(sim_time, header, body)

    def _is_reproposed_locked_block(self, header: BlockHeader, body: BlockBody | None) -> bool:
        """Return True if ``header`` re-proposes the identical content of ``self.locked_block``."""
        if self.locked_block is None:
            return False
        cached = self.block_cache.get(self.locked_block)
        if cached is None or cached[0] is None:
            return False
        locked_hdr, locked_body = cached
        if (
            header.height != locked_hdr.height
            or header.parent_hash != locked_hdr.parent_hash
            or header.state_hash != locked_hdr.state_hash
            or header.tx_root != locked_hdr.tx_root
        ):
            return False
        if body is not None and locked_body is not None:
            return body.transactions == locked_body.transactions
        return True

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
            is_valid_block, reason = self.ledger.validate_block(header, body, self.state)
            if not is_valid_block:
                self._log_reject(
                    sim_time,
                    EVENT_REJECT_BODY,
                    REASON_INVALID_BLOCK,
                    msg_type=MSG_BODY,
                    detail_extra=f"hash={header_hash.hex()[:16]} detail={reason}",
                )
                self.step = STEP_PREVOTE
                self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                self._schedule_prevote_timeout()
                return
        else:
            expected_parent = self._expected_parent_hash()
            if header.parent_hash != expected_parent:
                self._log_reject(
                    sim_time,
                    EVENT_REJECT_HEADER,
                    REASON_INVALID_BLOCK,
                    msg_type=MSG_HEADER,
                    detail_extra=f"hash={header_hash.hex()[:16]} parent_mismatch",
                )
                self.step = STEP_PREVOTE
                self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                self._schedule_prevote_timeout()
                return

            if body is not None:
                valid_txs, computed_state_hash = self.state.validate_and_compute_hash(
                    body.transactions
                )
                if not valid_txs or computed_state_hash != header.state_hash:
                    self._log_reject(
                        sim_time,
                        EVENT_REJECT_BODY,
                        REASON_INVALID_BLOCK,
                        msg_type=MSG_BODY,
                        detail_extra=f"hash={header_hash.hex()[:16]} state_mismatch",
                    )
                    self.step = STEP_PREVOTE
                    self.cast_vote(sim_time, PHASE_PREVOTE, NIL_BLOCK_HASH)
                    self._schedule_prevote_timeout()
                    return

        # 3. Check Locking Rules (Rule 4, 5)
        if self._is_reproposed_locked_block(header, body):
            can_prevote = True
        else:
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
            if (self.height, self.round) in self.prevoted_rounds:
                for prev in reversed(self.votes_cast):
                    if prev.height == self.height and prev.round == self.round and prev.phase == phase:
                        return prev
            self.prevoted_rounds.add((self.height, self.round))  # Rule 1
            event_type = EVENT_PREVOTE
        else:
            if (self.height, self.round) in self.precommitted_rounds:
                for prev in reversed(self.votes_cast):
                    if prev.height == self.height and prev.round == self.round and prev.phase == phase:
                        return prev
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

    @staticmethod
    def _map_vote_rejection_reason(reason: str) -> str:
        lower = reason.lower()
        if "equivocation" in lower:
            return REASON_EQUIVOCATION
        if "unknown validator" in lower:
            return REASON_UNKNOWN_SENDER
        if "height" in lower:
            return REASON_WRONG_HEIGHT
        if "round" in lower:
            return REASON_WRONG_ROUND
        return REASON_INVALID_SIGNATURE

    def on_vote(self, sim_time: float | int, vote: Vote) -> None:
        """Process incoming Vote packet."""
        if vote.voter not in self.validators:
            self._log_reject(
                sim_time,
                EVENT_REJECT_VOTE,
                REASON_UNKNOWN_SENDER,
                msg_type=MSG_VOTE,
                detail_extra=f"voter={vote.voter}",
            )
            return
        if vote.height > self.height:
            self.future_votes.setdefault(vote.height, []).append(vote)
            return
        if vote.height < self.height:
            self._log_reject(
                sim_time,
                EVENT_REJECT_VOTE,
                REASON_WRONG_HEIGHT,
                msg_type=MSG_VOTE,
                detail_extra=f"voter={vote.voter} got_h={vote.height}",
            )
            return
        if vote.round < self.round:
            self._log_reject(
                sim_time,
                EVENT_REJECT_VOTE,
                REASON_WRONG_ROUND,
                msg_type=MSG_VOTE,
                detail_extra=f"voter={vote.voter} got_r={vote.round}",
            )
            return

        # Check signature when public key is known and either signature is provided
        # or signature verification is strictly required by VoteTracker.
        pub_key = self.pub_keys.get(vote.voter)
        require_sigs = getattr(self.vote_tracker, "require_signatures", False)
        if pub_key is None and require_sigs:
            self._log_reject(
                sim_time,
                EVENT_REJECT_VOTE,
                REASON_UNKNOWN_SENDER,
                msg_type=MSG_VOTE,
                detail_extra=f"voter={vote.voter} missing_pubkey",
            )
            return
        if pub_key is not None and (vote.sig or require_sigs):
            if not vote.sig:
                self._log_reject(
                    sim_time,
                    EVENT_REJECT_VOTE,
                    REASON_INVALID_SIGNATURE,
                    msg_type=MSG_VOTE,
                    detail_extra=f"voter={vote.voter} empty_sig",
                )
                return
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
                self._log_reject(
                    sim_time,
                    EVENT_REJECT_VOTE,
                    REASON_INVALID_SIGNATURE,
                    msg_type=MSG_VOTE,
                    detail_extra=f"voter={vote.voter}",
                )
                return

        if vote.round > self.round:
            self.future_round_votes.setdefault((vote.height, vote.round), []).append(vote)
            return

        prev_rej = len(getattr(self.vote_tracker, "rejected_votes", ()))
        quorum_res = self.vote_tracker.add_vote(vote)
        after_rej = getattr(self.vote_tracker, "rejected_votes", ())
        if len(after_rej) > prev_rej:
            mapped_reason = self._map_vote_rejection_reason(after_rej[-1].reason)
            self._log_reject(
                sim_time,
                EVENT_REJECT_VOTE,
                mapped_reason,
                msg_type=MSG_VOTE,
                detail_extra=f"voter={vote.voter}",
            )
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
                    can_precommit = True
                    if hasattr(self.lock_manager, "can_precommit"):
                        can_precommit = self.lock_manager.can_precommit(
                            quorum_res.block_hash,
                            self.round,
                            height=self.height,
                        )
                    if not can_precommit:
                        return
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
                peers = [v.voter for v in quorum_res.votes if v.voter != self.node_id]
                if peers:
                    self.quorum_peers[quorum_res.block_hash] = peers
                self.pending_commits[quorum_res.block_hash] = (sim_time, self.height, self.round)
                self._finalize_block(sim_time, quorum_res.block_hash)
            else:
                # Quorum of Precommit NIL -> Advance round once
                if (self.height, self.round) not in self.round_advanced:
                    self.round_advanced.add((self.height, self.round))
                    self.start_round(sim_time, self.round + 1)

    def _validate_block_data(self, header: BlockHeader, body: BlockBody) -> tuple[bool, str]:
        """Validate block header and body before committing to state and ledger."""
        header_hash = self._compute_header_hash(header)
        if body.header_hash and body.header_hash != header_hash:
            return False, "Body header_hash does not match computed header hash"

        if hasattr(self.ledger, "validate_block"):
            return self.ledger.validate_block(header, body, self.state)

        expected_parent = self._expected_parent_hash()
        if header.parent_hash != expected_parent:
            return False, f"Parent hash mismatch: expected {expected_parent.hex()}, got {header.parent_hash.hex()}"

        if header.height != self.height:
            return False, f"Height mismatch: expected {self.height}, got {header.height}"

        if hasattr(self.state, "validate_and_compute_hash"):
            valid_txs, computed_state_hash = self.state.validate_and_compute_hash(body.transactions)
            if not valid_txs:
                return False, "Transactions validation failed"
            if header.state_hash and computed_state_hash != header.state_hash:
                return False, f"State hash mismatch: expected {header.state_hash.hex()}, got {computed_state_hash.hex()}"

        return True, "Valid"

    def _finalize_block(self, sim_time: float | int, block_hash: bytes) -> bool:
        """Validate and commit finalized block to state and ledger, then advance height."""
        if self.step == STEP_COMMIT or self.height in self.finalized_heights:
            return False

        cached = self.block_cache.get(block_hash)
        if not cached:
            # Block data not yet available: do NOT finalize or construct placeholder.
            if block_hash not in self.accepted_headers:
                if block_hash not in self.requested_headers:
                    self.requested_headers.add(block_hash)
                    req_hdr = pack_message(MSG_HEADER_REQUEST, block_hash)
                    targets = list(self.quorum_peers.get(block_hash, [])[:2])
                    proposer = self.get_proposer(self.height, self.round)
                    if proposer != self.node_id and proposer not in targets:
                        targets.append(proposer)
                    for peer in targets:
                        self.network.send(self.node_id, peer, req_hdr)
                return False

            hdr = self.headers_by_hash.get(block_hash)
            if hdr is not None:
                self._request_block_body(hdr, block_hash)
            return False

        header, body = cached
        if body is None:
            self._request_block_body(header, block_hash)
            return False

        is_valid, reason = self._validate_block_data(header, body)
        if not is_valid:
            return False

        self.step = STEP_COMMIT
        self.finalized_heights.add(self.height)
        self.pending_commits.pop(block_hash, None)

        self.state.commit_block(body)
        self.ledger.add_finalized_block(header, body)
        self.finalized_blocks.append((header, body))

        self._safe_log(
            sim_time,
            EVENT_FINALIZE,
            f"hash={block_hash.hex()[:16]} height={self.height}",
        )

        # Clear executed transactions from mempool
        self.mempool.remove_finalized(body.transactions, state=self.state)

        # Advance to next block height
        self.start_height(sim_time, self.height + 1)
        return True

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
        elif step == "BODY_RETRY":
            prop = self.proposals.get((height, round))
            if prop is not None and prop[0] is not None and prop[1] is None:
                hdr = prop[0]
                hdr_hash = self._compute_header_hash(hdr)
                if self.requested_bodies.get(hdr_hash, 0) < 8:
                    self._request_block_body(hdr, hdr_hash)
                    self._schedule_body_retry(height, round)

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

    def on_packet(
        self,
        sim_time: float | int,
        packet_bytes: bytes,
        sender: str = "",
    ) -> None:
        """Process incoming wire packet."""
        try:
            msg_type, payload = unpack_message(packet_bytes)
        except Exception:
            self._log_reject(
                sim_time,
                EVENT_REJECT_PACKET,
                REASON_MALFORMED_PACKET,
                msg_type="UNKNOWN",
                detail_extra=f"sender={sender or 'unknown'}",
            )
            return

        if msg_type == MSG_HEADER and isinstance(payload, BlockHeader):
            self.on_proposal_header(sim_time, payload, sender=sender)
        elif msg_type == MSG_BODY and isinstance(payload, BlockBody):
            self.on_proposal_body(sim_time, payload)
        elif msg_type == MSG_VOTE and isinstance(payload, Vote):
            self.on_vote(sim_time, payload)
        elif msg_type == MSG_TX and isinstance(payload, Transaction):
            self.admit_transaction(
                payload,
                source="network",
                sim_time=sim_time,
                sender=sender,
            )
        elif msg_type == MSG_HEADER_REQUEST and isinstance(payload, bytes):
            header = self.headers_by_hash.get(payload)
            if header is None and payload in self.block_cache:
                header = self.block_cache[payload][0]
            if header is not None and sender and sender != self.node_id and sender in self.validators:
                self.network.send(self.node_id, sender, pack_message(MSG_HEADER, header))
        elif msg_type == MSG_BODY_REQUEST and isinstance(payload, bytes):
            # Reply directly to requester only if full block (header + body) is cached.
            cached = self.block_cache.get(payload)
            if cached and cached[0] is not None and cached[1] is not None:
                if sender and sender != self.node_id and sender in self.validators:
                    reply = pack_message(MSG_BODY, cached[1])
                    self.network.send(self.node_id, sender, reply)
        else:
            self._log_reject(
                sim_time,
                EVENT_REJECT_PACKET,
                REASON_MALFORMED_PACKET,
                msg_type=str(msg_type),
                detail_extra=f"sender={sender or 'unknown'}",
            )


__all__ = ["ConsensusFSM"]
