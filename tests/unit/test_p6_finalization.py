"""Regression and validation tests for P6: Missing Block Finalization Integrity.

In Tendermint/PBFT, a +2/3 PRECOMMIT quorum proves validator agreement, but a
node cannot safely apply a block to state or ledger without having received and
validated the actual block header and body.

Tests:
1. P6-N: test_signed_precommit_quorum_cannot_finalize_missing_block
   Signed PRECOMMIT quorum arrives for an unknown block hash without header/body.
   Node MUST NOT synthesize a placeholder, commit to state/ledger, advance height,
   or mark finalized_count.
2. P6-P: test_late_valid_block_finalizes_after_precommit_quorum
   PRECOMMIT quorum arrives before block data. When the valid header and body
   arrive late, the node validates and finalizes the block exactly once, advancing
   ledger and consensus height.
3. P6-I: test_precommit_quorum_with_invalid_block_rejected
   PRECOMMIT quorum arrives, but late block has invalid signature/data.
   Node MUST NOT finalize.
4. P6-D: test_repeated_late_block_does_not_double_commit
   Late valid block delivered multiple times does not cause double-commit.
"""

from src.common.interfaces import (
    PHASE_PRECOMMIT,
    BlockBody,
    BlockHeader,
    Vote,
)
from src.consensus.node import ValidatorNode
from src.crypto.ed25519 import Ed25519Crypto
from src.execution.commitment import (
    compute_header_hash,
    compute_tx_root,
    header_signing_payload,
)
from src.security.locking import vote_signing_bytes


def test_signed_precommit_quorum_cannot_finalize_missing_block():
    crypto = Ed25519Crypto()
    validators = [f"node{i}" for i in range(4)]
    keys = {v: crypto.generate_keypair() for v in validators}
    public_keys = {v: pair[1] for v, pair in keys.items()}
    node = ValidatorNode.create_integrated(
        node_id="node0",
        validators=validators,
        pub_keys=public_keys,
        priv_key=keys["node0"][0],
        require_vote_signatures=True,
    )
    missing_block_hash = bytes([0x44]) * 32
    assert missing_block_hash not in node.fsm.block_cache
    assert node.ledger.height == 0

    # Three different validators produce a cryptographically valid precommit quorum.
    # Deliberately no header/body for this block has been received or validated.
    for voter in validators[1:]:
        unsigned = Vote(1, 0, PHASE_PRECOMMIT, missing_block_hash, voter)
        signature = crypto.sign(
            keys[voter][0], "VOTE:lab01", vote_signing_bytes(unsigned)
        )
        signed = Vote(1, 0, PHASE_PRECOMMIT, missing_block_hash, voter, sig=signature)
        node.fsm.on_vote(1.0, signed)

    assert node.vote_tracker.last_rejection is None
    assert node.ledger.height == 0, (
        "CRITICAL: node committed a block without a validated header/body"
    )
    assert node.finalized_count == 0
    assert node.fsm.height == 1
    assert 1 not in node.fsm.finalized_heights


def test_late_valid_block_finalizes_after_precommit_quorum():
    crypto = Ed25519Crypto()
    validators = [f"node{i}" for i in range(4)]
    keys = {v: crypto.generate_keypair() for v in validators}
    public_keys = {v: pair[1] for v, pair in keys.items()}
    node = ValidatorNode.create_integrated(
        node_id="node0",
        validators=validators,
        pub_keys=public_keys,
        priv_key=keys["node0"][0],
        require_vote_signatures=True,
    )
    node.start(0.0)

    parent_hash = node.ledger.get_latest_hash()
    state_hash = node.state.get_state_hash()
    tx_root = compute_tx_root([])
    proposer = "node1"

    unsigned_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
    )
    payload = header_signing_payload(unsigned_header)
    sig = crypto.sign(keys[proposer][0], "HEADER:lab01", payload)
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
        sig=sig,
    )
    valid_block_hash = compute_header_hash(signed_header)
    body = BlockBody(header_hash=valid_block_hash, transactions=())

    # Precommit quorum arrives FIRST before header/body
    for voter in validators[1:]:
        unsigned_vote = Vote(1, 0, PHASE_PRECOMMIT, valid_block_hash, voter)
        vote_sig = crypto.sign(
            keys[voter][0], "VOTE:lab01", vote_signing_bytes(unsigned_vote)
        )
        signed_vote = Vote(1, 0, PHASE_PRECOMMIT, valid_block_hash, voter, sig=vote_sig)
        node.fsm.on_vote(1.0, signed_vote)

    # Must NOT finalize missing block
    assert node.ledger.height == 0
    assert node.finalized_count == 0
    assert node.fsm.height == 1

    # Now valid header and body arrive
    node.fsm.on_proposal_header(2.0, signed_header)
    node.fsm.on_proposal_body(2.0, body)

    # Must now be finalized
    assert node.ledger.height == 1
    assert node.finalized_count == 1
    assert node.fsm.height == 2
    assert node.ledger.get_latest_hash() == valid_block_hash


def test_precommit_quorum_with_invalid_block_rejected():
    crypto = Ed25519Crypto()
    validators = [f"node{i}" for i in range(4)]
    keys = {v: crypto.generate_keypair() for v in validators}
    public_keys = {v: pair[1] for v, pair in keys.items()}
    node = ValidatorNode.create_integrated(
        node_id="node0",
        validators=validators,
        pub_keys=public_keys,
        priv_key=keys["node0"][0],
        require_vote_signatures=True,
    )
    node.start(0.0)

    # Construct invalid block with bad state hash
    parent_hash = node.ledger.get_latest_hash()
    bad_state_hash = bytes([0xEE]) * 32
    tx_root = compute_tx_root([])
    proposer = "node1"

    unsigned_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=bad_state_hash,
        tx_root=tx_root,
        proposer=proposer,
    )
    payload = header_signing_payload(unsigned_header)
    sig = crypto.sign(keys[proposer][0], "HEADER:lab01", payload)
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=bad_state_hash,
        tx_root=tx_root,
        proposer=proposer,
        sig=sig,
    )
    bad_block_hash = compute_header_hash(signed_header)
    body = BlockBody(header_hash=bad_block_hash, transactions=())

    # Quorum arrives for bad block
    for voter in validators[1:]:
        unsigned_vote = Vote(1, 0, PHASE_PRECOMMIT, bad_block_hash, voter)
        vote_sig = crypto.sign(
            keys[voter][0], "VOTE:lab01", vote_signing_bytes(unsigned_vote)
        )
        signed_vote = Vote(1, 0, PHASE_PRECOMMIT, bad_block_hash, voter, sig=vote_sig)
        node.fsm.on_vote(1.0, signed_vote)

    assert node.ledger.height == 0
    assert node.finalized_count == 0

    # Invalid header/body arrives
    node.fsm.on_proposal_header(2.0, signed_header)
    node.fsm.on_proposal_body(2.0, body)

    # Block must be rejected, not committed
    assert node.ledger.height == 0
    assert node.finalized_count == 0
    assert node.fsm.height == 1


def test_repeated_late_block_does_not_double_commit():
    crypto = Ed25519Crypto()
    validators = [f"node{i}" for i in range(4)]
    keys = {v: crypto.generate_keypair() for v in validators}
    public_keys = {v: pair[1] for v, pair in keys.items()}
    node = ValidatorNode.create_integrated(
        node_id="node0",
        validators=validators,
        pub_keys=public_keys,
        priv_key=keys["node0"][0],
        require_vote_signatures=True,
    )
    node.start(0.0)

    parent_hash = node.ledger.get_latest_hash()
    state_hash = node.state.get_state_hash()
    tx_root = compute_tx_root([])
    proposer = "node1"

    unsigned_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
    )
    payload = header_signing_payload(unsigned_header)
    sig = crypto.sign(keys[proposer][0], "HEADER:lab01", payload)
    signed_header = BlockHeader(
        height=1,
        round=0,
        parent_hash=parent_hash,
        state_hash=state_hash,
        tx_root=tx_root,
        proposer=proposer,
        sig=sig,
    )
    valid_block_hash = compute_header_hash(signed_header)
    body = BlockBody(header_hash=valid_block_hash, transactions=())

    for voter in validators[1:]:
        unsigned_vote = Vote(1, 0, PHASE_PRECOMMIT, valid_block_hash, voter)
        vote_sig = crypto.sign(
            keys[voter][0], "VOTE:lab01", vote_signing_bytes(unsigned_vote)
        )
        signed_vote = Vote(1, 0, PHASE_PRECOMMIT, valid_block_hash, voter, sig=vote_sig)
        node.fsm.on_vote(1.0, signed_vote)

    # Deliver header and body
    node.fsm.on_proposal_header(2.0, signed_header)
    node.fsm.on_proposal_body(2.0, body)
    assert node.ledger.height == 1
    assert node.finalized_count == 1

    # Deliver header and body again (redundant network packet)
    node.fsm.on_proposal_header(3.0, signed_header)
    node.fsm.on_proposal_body(3.0, body)
    assert node.ledger.height == 1
    assert node.finalized_count == 1
    assert node.fsm.height == 2
