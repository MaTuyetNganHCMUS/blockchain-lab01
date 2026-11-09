# blockchain-lab01
# Blockchain Lab 01

Minimal Layer 1 Blockchain & BFT Consensus Simulator.

## Requirements

- Python 3.10 or newer (tested with Python 3.12+)
- Git
- `cryptography` (installed by the project dependency)

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Run

```powershell
python -m pytest
python -m src
```

TV5 deterministic replay verification:

```powershell
python -m scripts.verify_tv5_determinism --seed 42 --output-dir logs/t8
```

The verified two-page TV5 report section is available at
`output/pdf/tv5-locking-byzantine-report.pdf`.

The project is split into five ownership areas. Shared data contracts, typing protocols,
and basic mocks belong in `src/interfaces.py` and `src/common/` (with basic mocks in `src/common/mocks.py`).
Implementations should preserve those contracts.

## TV1 cryptography

The current package layout keeps TV1 under `src/crypto/`:

- `src/common/serializer.py`: tagged canonical encoding. Integers use signed
  8-byte big-endian encoding, strings and bytes are length-prefixed, dataclass
  fields preserve declaration order, and dictionary entries are sorted by
  canonical key bytes.
- `src/crypto/ed25519.py`: Ed25519 signing/verification and SHA-256.
- `src/crypto/keys.py`: fixed validator-to-public-key registry.
- `src/common/models.py`: shared immutable domain-model re-exports.

Signatures always include the domain in the signed bytes. Use explicit domains
such as `TX:<chain_id>`, `HEADER:<chain_id>`, and `VOTE:<chain_id>`. A
signature from one domain or chain cannot verify in another.

```text
blockchain-lab01/
|-- config/                  Sample simulator configuration
|-- docs/                    Design notes and diagrams
|-- scripts/                 Developer helper scripts
|-- src/
|   |-- interfaces.py        Unified public contracts & mocks facade
|   |-- common/              Shared contracts, models, serialization & mocks
|   |   |-- interfaces.py    Core data models (frozen) & typing.Protocols
|   |   |-- mocks.py         Mocks/Stubs for all 5 modules (TV1..TV5)
|   |   |-- models.py        Domain model re-exports
|   |   `-- serializer.py    Canonical encoding hook
|   |-- crypto/              TV1: Ed25519, SHA-256, canonical encoding & keys
|   |-- common/              Shared models, contracts, and canonical serializer
|   |-- execution/           TV2: state and ledger
|   |-- network/             TV3: simulator, gossip, rate limiting
|   |-- consensus/           TV4: FSM and validator node
|   |-- security/            TV5: locking and Byzantine behaviors
|   `-- observability/       TV5: deterministic logging
|-- tests/
|   |-- unit/
|   `-- e2e/
|-- pyproject.toml
`-- .gitignore
```

## Branches

- `main`: stable integration branch
- `feature/tv1-crypto-models`
- `feature/tv2-state-ledger`
- `feature/tv3-network-gossip`
- `feature/tv4-consensus-node`
- `feature/tv5-locking-byzantine`
