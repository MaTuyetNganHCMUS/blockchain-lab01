# blockchain-lab01
# Blockchain Lab 01

Minimal Layer 1 Blockchain & BFT Consensus Simulator.

## Requirements

- Python 3.10 or newer (tested with Python 3.12)
- Git

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

The project is split into five ownership areas. Shared data contracts belong in
`src/common/interfaces.py`; implementations should preserve those contracts.

```text
blockchain-lab01/
|-- config/                  Sample simulator configuration
|-- docs/                    Design notes and diagrams
|-- scripts/                 Developer helper scripts
|-- src/
|   |-- common/              Shared contracts, models, serialization
|   |-- crypto/              TV1: cryptography
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