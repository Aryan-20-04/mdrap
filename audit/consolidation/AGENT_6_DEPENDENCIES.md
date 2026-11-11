# MDRAP Consolidation — Agent 6: Dependency Minimization & Configuration Architecture

## 1. Executive Summary

Agent 6 audited runtime dependencies, packaging manifests, and configuration parsing architectures.

Core Design Rule (§26 of Specification): **Zero Mandatory Runtime Dependencies**.
The core MDRAP platform (normalization gateway, 7-rule quality validator, cross-reconciler, and WAL storage) must run 100% on the Python standard library.

---

## 2. Dependency Audit & Target Groupings

| Package | Status in Core | Optional Group | Rationale |
| :--- | :--- | :--- | :--- |
| **`pyyaml`** | **ELIMINATED FROM CORE** | `[config]` / `[yaml]` | Standard library `tomllib` (Python >= 3.11) and `json` provide zero-dependency configuration. `config.py` includes a robust stdlib fallback parser for legacy `.yaml`. |
| **`duckdb`** | **OPTIONAL EXTRA** | `[columnar]` | Embedded OLAP database for large historical tick queries. SQLite WAL remains the canonical, zero-dependency storage engine. |
| **`pyarrow`** | **OPTIONAL EXTRA** | `[export]` / `[columnar]` | Parquet serialization. Core persistence and evidence verification operate without PyArrow. |
| **`rich`** | **OPTIONAL (CORE CLI)**| `[ui]` | Institutional terminal telemetry. Retained in core CLI with guaranteed stdlib plain-text fallback in `term.py`. |
| **`websockets`**| **OPTIONAL EXTRA** | `[feeds]` | Real-time WebSocket feed handlers for public exchanges (Coinbase, Binance, Kraken). |
| **`fastapi`**, **`uvicorn`**, **`httpx`** | **OPTIONAL EXTRA** | `[api]` | REST & WebSocket distribution sidecar. |

---

## 3. Configuration Architecture: TOML, JSON, and Legacy YAML

### Problem Statement
Silent format deprecation breaks customer deployments. If a customer has an existing `config.yaml`, the system must not crash or refuse to load.

### Solution: Multi-Format Config Loader with Zero Hard Dependencies
1. **First-Class TOML Support (`config.toml`)**:
   - Parsed natively using Python's standard library `tomllib` (Python 3.11+).
   - Canonical format for all new deployments and templates.
2. **Machine-Readable JSON Support (`config.json`)**:
   - Parsed natively using Python's standard library `json`.
   - Ideal for CI/CD and programmatic container configuration.
3. **Graceful Legacy YAML Support (`config.yaml`)**:
   - If `pyyaml` is installed, it is utilized.
   - If `pyyaml` is NOT installed, `src/mdrap/config.py` uses an internal stdlib line/regex parser capable of parsing standard key-value, nested dictionaries, and lists.
   - Clear warning emitted informing the operator that TOML is recommended.

---

## 4. Packaging Manifest Updates (`pyproject.toml`)

```toml
[project]
name = "mdrap-core"
requires-python = ">=3.10"
dependencies = [
    # MDRAP core platform has zero mandatory external dependencies (pure Python stdlib).
]

[project.optional-dependencies]
ui = [
    "rich>=13.0.0",
]
feeds = [
    "websockets>=12.0,<17.0",
]
api = [
    "fastapi>=0.110.0",
    "uvicorn>=0.28.0",
    "httpx>=0.27.0",
]
columnar = [
    "duckdb>=1.0.0",
    "pyarrow>=20.0.0",
]
export = [
    "pyarrow>=20.0.0",
]
yaml = [
    "pyyaml>=6.0",
]
all = [
    "rich>=13.0.0",
    "websockets>=12.0,<17.0",
    "fastapi>=0.110.0",
    "uvicorn>=0.28.0",
    "httpx>=0.27.0",
    "duckdb>=1.0.0",
    "pyarrow>=20.0.0",
    "pyyaml>=6.0",
]
```

---

## 5. Verification of Clean Pure-Stdlib Runtime

With zero optional dependencies installed:
- `python cli.py version`: Runs cleanly via stdlib.
- `python cli.py status`: Runs cleanly via stdlib `term.py` fallback.
- `python cli.py doctor`: Runs cleanly via stdlib.
- `python cli.py replay`: Runs deterministic incident replay via stdlib SQLite.
- `python cli.py verify`: Runs Merkle audit tree verification via stdlib `hashlib`.
- Ingestion, validation, reconciliation, and WAL persistence execute with 100% fidelity.
