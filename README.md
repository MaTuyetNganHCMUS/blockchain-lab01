# blockchain-lab01
# Blockchain Lab 01

Minimal Layer 1 Blockchain & BFT Consensus Simulator (`n = 3f + 1`, Tendermint-style 2-phase consensus, partial synchrony, and deterministic execution).

## Requirements

- Python 3.10 or newer (tested with Python 3.12+ / 3.14)
- Git
- `cryptography>=42.0` (`pytest>=8.0`, `ruff>=0.6` for development/testing)

## Setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks script activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Linux / macOS (bash/zsh)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Run Simulator (CLI)

Run an integrated 10-validator (`n = 10, f = 3, quorum = 7`) simulation from the CLI:

```bash
python -m src --scenario normal --config config/default.json --seed 42 --output logs/run_normal
```

Available `--scenario` choices:
- `normal` (T1: 10 validators, TX submitted to non-proposer, TX gossip, Header-before-Body pull, 2-phase finality)
- `duplicate_reorder` (T2: duplicate & reordered packet resilience)
- `invalid_signature` (T3: invalid signature, wrong domain, malformed packet rejection logging)
- `replay` (T4: replay prevention before and after finality)
- `partial_synchrony` (T5: pre-GST loss/jitter safety & post-GST liveness)
- `silent_proposer` (T6: round timeout & proposer rotation)
- `byzantine_equivocation` (T7: up to `f` Byzantine equivocators)
- `determinism` (T8: two runs with identical seed compared for byte-identical logs & state hash)
- `all` (runs all 8 scenarios sequentially and exits non-zero if any invariant fails)

## Run Tests (Single Entry Point)

Run the entire unit + E2E suite (T1–T8 and regression tests):

```bash
python -m pytest
```

Standalone TV5 T7/T8 determinism script:

```bash
python -m scripts.verify_tv5_determinism --seed 42 --output-dir logs/t8
```

The verified two-page TV5 report section is available at `output/pdf/tv5-locking-byzantine-report.pdf`.

## Project Structure

```text
blockchain-lab01/
|-- config/                  Simulator configuration (`default.json`, `network_t5.json`, `network_unreliable.json`)
|-- docs/                    Design notes and module reports
|-- logs/                    Sample traces and deterministic T8 evidence
|-- scripts/                 Verification & report generation helpers
|-- src/
|   |-- __main__.py          CLI simulator entry point (`python -m src`)
|   |-- simulation.py        Integrated scenario runner & config loader
|   |-- interfaces.py        Unified public contracts & mocks facade
|   |-- common/              Core frozen models, protocols, serializer & mocks
|   |-- crypto/              TV1: Ed25519, SHA-256, canonical encoding & keys
|   |-- execution/           TV2: state engine, commitments, ledger & bounded mempool
|   |-- network/             TV3: discrete-event simulator, gossip & rate limiting
|   |-- consensus/           TV4: consensus FSM, wire codec & validator node
|   |-- security/            TV5: BFT quorum validation, locking rules & Byzantine simulator
|   `-- observability/       TV5: byte-identical deterministic event logger
|-- tests/
|   |-- unit/                Unit & regression tests
|   `-- e2e/                 End-to-end T1-T8 & integrated cluster tests
`-- pyproject.toml
```

## TV1 Cryptography Notes

- `src/common/serializer.py`: tagged canonical encoding (8-byte signed big-endian integers, length-prefixed strings/bytes, declaration-ordered dataclasses, sorted dictionary entries).
- `src/crypto/ed25519.py`: Ed25519 signing/verification and SHA-256 with domain separation (`TX:<chain_id>`, `HEADER:<chain_id>`, `VOTE:<chain_id>`).
- `src/crypto/keys.py`: fixed validator-to-public-key registry.
