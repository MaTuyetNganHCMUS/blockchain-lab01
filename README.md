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
`src/interfaces.py`; implementations should preserve those contracts.

```text
blockchain-lab01/
|-- config/                  Sample simulator configuration
|-- docs/                    Design notes and diagrams
|-- scripts/                 Developer helper scripts
|-- src/
|   |-- interfaces.py        Shared contracts
|   |-- models.py            Data models
|   |-- crypto.py            TV1: cryptography
|   |-- serializer.py        TV1: canonical encoding
|   |-- state.py              TV2: execution/state
|   |-- ledger.py             TV2: ledger view
|   |-- network.py            TV3: network simulator
|   |-- gossip.py             TV3: header/body gossip
|   |-- rate_limiter.py       TV3: rate limiting
|   |-- consensus_fsm.py      TV4: consensus FSM
|   |-- node.py               TV4: validator node
|   |-- locking.py            TV5: locking and votes
|   |-- byzantine.py          TV5: Byzantine behaviors
|   `-- logger.py             TV5: deterministic logging
|-- tests/
|   |-- unit/
|   `-- e2e/
|-- pyproject.toml
`-- .gitignore
```

## Branches

- `main`: stable integration branch
- `develop`: shared development branch
- `feature/tv1-crypto-models`
- `feature/tv2-state-ledger`
- `feature/tv3-network-gossip`
- `feature/tv4-consensus-node`
- `feature/tv5-locking-byzantine`