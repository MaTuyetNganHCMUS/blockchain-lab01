# TV5 — Locking, Byzantine Faults, and Deterministic Logs

## Implementation notes

TV5 implements the safety boundary between untrusted consensus messages and the
validator state machine. `VoteTracker` first validates a vote, indexes it by
`(height, round, phase)`, and admits at most one vote from each validator into
that index. An exact retransmission is idempotent. A second vote for another
block is recorded as equivocation evidence but cannot replace or supplement the
first vote. A quorum result is produced only when one block hash has at least
`2f + 1` distinct voters, where `f = floor((n - 1) / 3)`.

The seven rules are represented as follows:

1. A validator can record only one local PREVOTE for each `(H, R)`.
2. A validator can record only one local PRECOMMIT for each `(H, R)`.
3. A non-NIL PRECOMMIT is permitted only after a matching PREVOTE quorum/PoLC.
4. A valid non-NIL PREVOTE quorum updates `locked_block` and `locked_round`.
5. A different block is eligible for PREVOTE only when its PoLC round is newer
   than the current lock and older than the current proposal round.
6. Unknown voters, invalid height/round/phase/hash, and signatures made under a
   wrong `VOTE:<chain_id>` domain are rejected before tallying.
7. Duplicate messages are ignored; conflicting duplicates become audit evidence
   and still count no more than one vote.

Signature checking is enabled when the tracker receives a crypto provider and
public-key map. The signed payload contains every unsigned Vote field in a
stable JSON encoding. This prevents a signature from being reused after changing
the voter, height, round, phase, or block hash. `expected_height` lets the FSM
reject stale and future messages without contaminating the current height.

`LockManager` keeps PoLC certificates separately from the local signing record.
It checks that a certificate has distinct voters and internally consistent
height, round, phase, and block hash. Calling `start_height()` clears the old
height's lock, while the historical signing record remains available for audit.
The integration layer may require a recorded PoLC (`require_recorded_polka=True`)
instead of trusting only the proposal's `polka_round` metadata.

## Byzantine simulator and difficult cases

`ByzantineAttackSimulator` refuses a configuration containing more than
`f = floor((n - 1) / 3)` faulty validators. It provides four deterministic fault
primitives: split-view equivocation, an invalid state commitment, replay of an
old payload, and silence. Recipient lists are sorted before splitting, so the
same inputs always generate the same attack trace. An optional callback re-signs
both conflicting votes, allowing integration with TV1's Ed25519 implementation.

The main subtlety is that equivocation detection must not remove the first vote
and insert the second. Replacement would let packet order change the tally and
could manufacture conflicting quorums in different node views. The tracker
therefore keeps the first valid vote and stores the pair only as evidence.
Another subtlety is that a PoLC cannot justify a proposal in the same round: the
required ordering is `locked_round < polka_round < proposal_round`.

`DeterministicLogger` normalizes simulated time with decimal round-half-even to
two places, serializes structured detail as sorted compact JSON, escapes line
breaks in strings, and writes UTF-8 with LF plus one final newline. It never uses
wall-clock time. These choices make log SHA-256 values independent of locale,
platform newline conventions, dictionary insertion order, and floating-point
display differences.

## Test results

The TV5 unit suite covers ten duplicate deliveries, equivocation evidence,
height/round/phase/voter rejection, chain-specific signature domains, separate
PREVOTE/PRECOMMIT slots, lock/unlock behavior, Rule 3 precommit gating, all four
Byzantine behaviors, the `f` bound, and byte-identical logger output.

Test T7 runs four validators with one equivocator. Node0 sends a vote for block A
to one half and block B to the other; all honest validators vote for A. Both
observer views finalize exactly one common block, so no fork is produced.

Test T8 runs T7 twice with seed 42. Both runs produce log SHA-256
`3c5c2ac64672dc7a25746c1d5220d10cbe34f18277423def81a02bd2b9560afa` and final
state hash `654698a006e8cdee6c249d932aa088ddbbc1cad4098284e52fb241b61f3b2f34`.
The standalone command is:

```bash
python -m scripts.verify_tv5_determinism --seed 42 --output-dir logs/t8
```

At completion, the repository test suite reports 33 passing tests and Ruff
reports no findings in the TV5 source, tests, or script.
