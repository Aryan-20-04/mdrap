# MDRAP Installation & Build Guide

This document details the installation options, build requirements, optional dependencies, companion packages, and Docker containerization workflows for MDRAP.

---

## 1. The Packaging Reality

> [!IMPORTANT]
> **MDRAP is NOT currently published to the public PyPI repository.**
> Attempting to run `pip install mdrap` from PyPI will either fail or install an unrelated package. 
> MDRAP must be installed directly from a Git source checkout, an internal private artifact repository, or locally built wheel distributions.

Python **3.10** or higher is required (tested up to Python 3.13).

---

## 2. Source Installation

To install MDRAP from a local git repository:

```bash
# Clone the repository
git clone https://github.com/mdrap-org/mdrap.git
cd mdrap

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Upgrade packaging tools
python -m pip install --upgrade pip setuptools wheel
```

### Installation Flavors & Extras

MDRAP core follows a **zero-mandatory-dependency** philosophy. The core normalization, quality engine, reconciliation, WAL writer, and basic CLI execute using only Python's standard library. Additional features are activated via optional extras:

| Extra | Dependencies Installed | Purpose |
| --- | --- | --- |
| `pip install -e .` | None (stdlib only) | Core normalization, validation, WAL storage, and baseline CLI. |
| `pip install -e .[api]` | `fastapi`, `uvicorn`, `anyio` | Production REST API and WebSocket streaming server (`mdrap serve`). |
| `pip install -e .[ui]` | `rich` | Rich terminal UI formatting, colorized tables, and interactive desk navigator. |
| `pip install -e .[all]` | `fastapi`, `uvicorn`, `anyio`, `rich` | Complete platform features for development and production operations. |
| `pip install -e .[dev]` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy` | Test runner, linters, and type checkers. |

---

## 3. Companion Packages

Specialized financial microstructure analytics, options pricing, algorithmic execution, and maritime intelligence are segregated into modular companion packages located in `packages/`:

```bash
# Options pricing, binomial trees, implied volatility solver, and Greeks
python -m pip install -e packages/mdrap-options

# Transaction Cost Analysis (TCA), Implementation Shortfall, and broker scorecards
python -m pip install -e packages/mdrap-analytics

# Algorithmic execution strategies, Avellaneda-Stoikov market-making, and risk guards
python -m pip install -e packages/mdrap-strategies

# AIS vessel tracking, haversine geospatial calculations, and maritime chokepoints
python -m pip install -e packages/mdrap-contrib-vessel
```

All companion packages can be imported directly (e.g. `import mdrap_options`) or accessed through backward-compatible shims in `mdrap.*` (e.g. `from mdrap.options import OptionsChain`).

---

## 4. Native C Hot-Path Kernel Build

MDRAP includes an optional high-performance C kernel (`src/fastpath.c`, `src/mdrap_core.c`) providing:
- SIMD AVX2-accelerated SBE message decoding.
- Lock-free SPSC shared memory ring buffer with 64-byte cacheline alignment.
- Zero-copy microsecond event normalization and ring buffer publishing.

### Compiling the Native Extension

Compilation requires a platform C compiler (GCC or Clang on Linux/macOS, Microsoft Visual C++ Build Tools on Windows):

```bash
python build_fastpath.py
```

Upon successful compilation, the shared library (`_fastpath.so` or `_fastpath.pyd`) is placed into `src/mdrap/`.

### Pure Python Fallback & Parity Guarantee

Native compilation is strictly optional. If a C compiler is unavailable or if native execution is disabled via the environment variable:

```bash
export MDRAP_DISABLE_FASTPATH="1"  # PowerShell: $env:MDRAP_DISABLE_FASTPATH="1"
```

MDRAP automatically switches to the pure Python implementation with **100% numerical and behavioral parity**. All 24 quality checks, deduplication logic, and validation outcomes produce identical results regardless of whether native acceleration is enabled.

---

## 5. Docker Containerization

A multi-stage, hardened `Dockerfile` is included for containerized environments.

### Building the Docker Image

```bash
docker build -t mdrap:latest .
```

### Running the Container

The default container runs `mdrap serve` listening on port `8000`:

```bash
docker run -d \
  --name mdrap-instance \
  -p 8000:8000 \
  -e MDRAP_API_KEY_SALT="your-cryptographic-salt-min-16-chars" \
  -e MDRAP_INITIAL_ADMIN_KEY="admin-secret-key-token-here" \
  -v mdrap-data:/data \
  mdrap:latest
```

### Multi-Container Compose Setup

Using `docker-compose.yml`:

```bash
docker compose up -d
```

To enable the optional Caddy TLS reverse proxy profile:

```bash
docker compose --profile tls up -d
```

### Container Verification

Check container health status:

```bash
curl -f http://127.0.0.1:8000/v1/health
```
