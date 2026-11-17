# Blockchain Lab 01

Minimal Layer 1 Blockchain & BFT Consensus Simulator (`n = 3f + 1`, Tendermint-style 2-phase consensus, partial synchrony, and deterministic execution).

## Requirements

- Python 3.10 or newer (tested on Python 3.12+ / 3.14)
- Git
- Dependencies: `cryptography>=42.0`, `pytest>=8.0`

## Setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "cryptography>=42.0" "pytest>=8.0"
```

If PowerShell blocks script execution:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux / macOS (bash/zsh)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "cryptography>=42.0" "pytest>=8.0"
```

---

## Quick Start (Run All Scenarios)

Run all 8 consensus, network, and security scenarios (T1–T8) in a single integrated execution:

```bash
python -m src --scenario all --config config/default.json --seed 42 --output logs/all_scenarios
```

- Sequentially executes all 8 scenarios: `normal`, `duplicate_reorder`, `invalid_signature`, `replay`, `partial_synchrony`, `silent_proposer`, `byzantine_equivocation`, and `determinism`.
- Automatically outputs deterministic log traces into `logs/all_scenarios/<scenario>/run.log`.
- Exits with code `0` if all invariants and assertions hold.

---

## Run Individual Scenarios (CLI)

You can copy and run any specific scenario command directly. All commands output execution evidence and logs to `logs/<scenario_name>/run.log`.

### T1: Normal 10-Validator Consensus
*10 validators (`n = 10, f = 3, quorum = 7`), TX submitted to non-proposer, TX gossip, Header-before-Body pull, 2-phase finality & commit.*
```bash
python -m src --scenario normal --config config/default.json --seed 42 --output logs/t1_normal
```
- **Log Evidence:** `logs/t1_normal/run.log`

### T2: Duplicate & Reordered Packets
*Resilience against duplicated and out-of-order network messages.*
```bash
python -m src --scenario duplicate_reorder --config config/default.json --seed 42 --output logs/t2_duplicate_reorder
```
- **Log Evidence:** `logs/t2_duplicate_reorder/run.log`

### T3: Invalid Signatures & Malformed Packets
*Rejection and logging of invalid Ed25519 signatures, wrong domain separators, and malformed wire messages.*
```bash
python -m src --scenario invalid_signature --config config/default.json --seed 42 --output logs/t3_invalid_signature
```
- **Log Evidence:** `logs/t3_invalid_signature/run.log`

### T4: Replay Prevention
*Replay prevention for identical transactions submitted pre-finality and post-finality.*
```bash
python -m src --scenario replay --config config/default.json --seed 42 --output logs/t4_replay
```
- **Log Evidence:** `logs/t4_replay/run.log`

### T5: Partial Synchrony (Pre-GST & Post-GST)
*Safety maintained under pre-GST message delay/drop; liveness achieved post-GST.*
```bash
python -m src --scenario partial_synchrony --config config/default.json --seed 42 --output logs/t5_partial_synchrony
```
- **Log Evidence:** `logs/t5_partial_synchrony/run.log`

### T6: Silent Proposer & Round Timeout
*Proposer failure detection via round timeout and deterministic proposer rotation.*
```bash
python -m src --scenario silent_proposer --config config/default.json --seed 42 --output logs/t6_silent_proposer
```
- **Log Evidence:** `logs/t6_silent_proposer/run.log`

### T7: Byzantine Equivocation
*Consensus safety maintained with up to `f = 3` Byzantine equivocators broadcasting conflicting proposals.*
```bash
python -m src --scenario byzantine_equivocation --config config/default.json --seed 42 --output logs/t7_byzantine_equivocation
```
- **Log Evidence:** `logs/t7_byzantine_equivocation/run.log`

### T8: Deterministic Execution
*Executes two full runs with identical seed (`seed=42`) and validates byte-identical log traces and state hashes.*
```bash
python -m src --scenario determinism --config config/default.json --seed 42 --output logs/t8_determinism
```
- **Log Evidence:** `logs/t8_determinism/run.log`

---

## Run Test Suite (Unit & E2E)

Run the comprehensive test suite covering all unit tests, scenarios T1–T8, Header-before-Body verification, and Byzantine determinism:

```bash
python -m pytest tests -v
```

Or run without verbose output:

```bash
python -m pytest
```

To run a specific test suite:
- Unit tests: `python -m pytest tests/unit -v`
- End-to-End tests: `python -m pytest tests/e2e -v`
- TV5 Byzantine & Determinism: `python -m pytest tests/e2e/test_tv5_byzantine_determinism.py -v`

---

## Submission Packaging & Verification

To verify that the project is completely self-contained and builds a valid submission archive matching all course requirements:

```bash
./prepare_submission.sh
```

The script automatically:
1. Prepares the submission directory with only allowed folders and files (`REPORT.pdf`, `README.md`, `config/`, `logs/`, `src/`, `tests/`).
2. Creates an isolated temporary virtual environment and installs dependencies (`cryptography`, `pytest`).
3. Executes the full test suite in the isolated environment to guarantee zero external/missing dependencies.
4. Compares legal and test file manifests to ensure 100% byte-identical consistency.
5. Compresses the verified submission into `submission/Lab01_23120029_23120048_23120282_23120308.zip`.

---

## Project Structure

```text
blockchain-lab01/
|-- REPORT.pdf               Lab 01 Final Report
|-- README.md                Execution guide and project overview
|-- config/                  Simulation configurations (default.json, network_t5.json, network_unreliable.json)
|-- logs/                    Sample log traces and deterministic execution evidence
|-- src/
|   |-- __main__.py          CLI entry point (python -m src)
|   |-- simulation.py        Scenario runner & configuration loader
|   |-- interfaces.py        Unified interfaces & domain contracts
|   |-- common/              Data models, serialization & canonical encoding
|   |-- crypto/              TV1: Ed25519 signing/verifying & SHA-256 domain separation
|   |-- execution/           TV2: State engine, balance ledger & bounded mempool
|   |-- network/             TV3: Discrete-event simulator & packet delivery queue
|   |-- consensus/           TV4: Tendermint-style 2-phase BFT FSM & Header-before-Body
|   |-- security/            TV5: Byzantine simulator, equivocation defense & determinism verifier
|   `-- observability/       TV5: Structured deterministic event logging
`-- tests/
    |-- unit/                Unit tests (crypto, execution, network, consensus, security)
    `-- e2e/                 End-to-end integration tests (T1-T8 scenarios)
```

## Module Notes

- **TV1 Cryptography (`src/crypto/`, `src/common/serializer.py`):**
  - Canonical tagged encoding (big-endian integers, length-prefixed strings/bytes, declaration-ordered dataclasses, sorted dictionaries).
  - Ed25519 digital signatures and SHA-256 with domain separation prefixes (`TX:<chain_id>`, `HEADER:<chain_id>`, `VOTE:<chain_id>`).
  - Key registry mapping validator IDs to public keys.

- **TV2 Execution Engine (`src/execution/`):**
  - Account state management and transfer execution with nonce checks.
  - State root calculation and deterministic block execution.
  - Bounded mempool with FIFO eviction and replay prevention.

- **TV3 Network Simulation (`src/network/`):**
  - Discrete-event network simulation with configurable latency, jitter, and packet loss.
  - Partition modeling (pre-GST and post-GST transitions).

- **TV4 BFT Consensus (`src/consensus/`):**
  - Tendermint-style 2-phase consensus FSM (`PROPOSE` -> `PREVOTE` -> `PRECOMMIT` -> `COMMIT`).
  - Header-before-Body protocol (`ConsensusFSM` + `ValidatorNode`): nodes broadcast and vote on compact BlockHeaders, pulling full BlockBodies on-demand before commit.
  - Locking and unlocking rules to ensure safety under partial synchrony.

- **TV5 Security & Determinism (`src/security/`, `src/observability/`):**
  - Resilience against up to $f = \lfloor(n-1)/3\rfloor$ Byzantine equivocators.
  - Deterministic event logging with SHA-256 digest comparison across executions with identical seeds.
