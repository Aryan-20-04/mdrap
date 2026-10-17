# MDRAP Platform Editions & Maintenance Guide

**Document Version:** 1.0.0  
**Effective Date:** 2026-10-15  
**Applies To:** MDRAP Free/Core Engine (`mdrap-core`) and Commercial/Companion Extensions  
**Audience:** Operators, Quantitative Developers, Architecture Maintainers, and Commercial Licensees

---

## 1. Architectural Philosophy & Strategy

MDRAP follows a clean **Open-Core Architecture**:

1. **Free / Open-Source Core (`mdrap-core`)**:
   - **100% Permissive MIT License**.
   - Built exclusively on Python standard library primitives with an optional native C hot-path accelerator (`fastpath.c`).
   - Focused strictly on **single-node market data reliability**: normalization, schema validation, anomaly detection, atomic Write-Ahead Log (WAL) durability, and sub-microsecond shared memory IPC.
   - Contains **zero commercial dependencies, zero license verification servers, and zero phone-home telemetry**.
   - Hosted publicly on GitHub.

2. **Commercial / Companion Packages (`packages/`)**:
   - Dedicated modules for specialized institutional workflows: quantitative derivatives pricing, regulatory Transaction Cost Analysis (TCA), portfolio risk management, algorithmic strategy execution, and maritime intelligence.
   - Maintained in private repositories and distributed independently to commercial clients.
   - Built as passive downstream consumers that connect cleanly into the core engine via standard IPC, WAL files, or Python APIs.

---

## 2. Comprehensive Feature Matrix

| Functional Domain | Free / Open-Source Core (`mdrap-core`) | Commercial / Companion Packages | Module Location & Tier |
|---|---|---|---|
| **License & Redistribution** | **100% MIT License**; unrestricted commercial and private use. | **Commercial / Proprietary / Add-on License**; private distribution. | Core: Root / `src/mdrap`<br>Commercial: `packages/` |
| **External Dependencies** | **Zero mandatory dependencies** (pure Python stdlib + optional C compiler). | May depend on domain libraries (`numpy`, `scipy`, `pyarrow`, etc.). | Core: `pyproject.toml` |
| **Feed Ingestion & Protocols** | NASDAQ ITCH, Simple Binary Encoding (SBE), CME MDP 3.0 SBE decoder (`cme.py`), Eurex T7 decoder (`eurex.py`), WebSockets (Binance, Kraken, Coinbase), REST snapshots, PCAP playback. | Hardware kernel-bypass feeds (Solarflare EF_VI / DPDK multicast A/B feed arbiters, OPRA binary feeds, ICE iMpact). | Core: `src/mdrap/gateway*.py`, `src/mdrap/sbe.py`, `src/mdrap/itch.py`, `src/mdrap/cme.py`, `src/mdrap/eurex.py` |
| **Validation & Quality Engine** | **7 core rule dimensions (15 rule definitions MD001–MD015)**: Crossed quotes, sequence gaps, duplicates, negative prices, future/stale timestamps, abnormal spread, Welford statistical sigma filter. Non-downgradable quality states (`INVALID > SUSPICIOUS > VALID`). | Advanced ML-driven spoofing detection, quote stuffing heuristics, latency arbitrage pattern filters. | Core: `src/mdrap/quality.py`, `rules.def`, `rules.py` |
| **Hot-Path Acceleration** | Native C SIMD (AVX2) hot-path kernel (`fastpath.c`) with **100% pure-Python fallback parity**. | Custom FPGA / ASIC register maps, kernel-bypass Solarflare EF_VI / DPDK drivers. | Core: `src/mdrap/fastpath.c`, `src/mdrap/mdrap_core.c` |
| **Durability & Persistence** | High-throughput segmented Write-Ahead Log (WAL) with **IEEE 802.3 CRC-32**, atomic batch commits, and automatic crash-tail salvage (`IngestLog`). SQLite WAL projection. | Columnar historical Parquet archives (`chd.py`), ClickHouse, TimescaleDB, and cloud S3 tiered offloaders. | Core: `src/mdrap/ingestlog.py`, `src/mdrap/storage.py` |
| **IPC & Tick Distribution** | **Lock-free SPSC Seqlock Shared Memory ring buffer** (`/dev/shm` / Windows named mapping, 128-byte slots) and localhost TCP streaming socket fan-out. | Distributed multi-node message fabric, WAN cross-region replication, low-latency multicast brokers. | Core: `src/mdrap/shm.py`, `src/mdrap/gateway_tcp.py` |
| **Derivatives & Options Pricing** | **None** (Out of core scope). | **Black-Scholes-Merton (BSM)**, Cox-Ross-Rubinstein (CRR) binomial trees for American options, Greeks ($\Delta, \Gamma, \Theta, \mathcal{V}, \rho$), Implied Volatility solver, Options chains. | Commercial: `packages/mdrap-options/`<br>Shim: `src/mdrap/options.py` |
| **Transaction Cost Analysis (TCA)** | **None** (Out of core scope). | Sub-microsecond slippage vs NBBO, **SEC Rule 605/606** and **MiFID II RTS 27/28** compliance reporting, Effective vs Quoted spread, Broker scorecards. | Commercial: `packages/mdrap-analytics/`<br>Shim: `src/mdrap/tca.py` |
| **Portfolio Risk Engine** | **None** (Out of core scope). | Historical, Parametric, and Monte Carlo **Value-at-Risk (VaR)**, Expected Shortfall (CVaR), Portfolio drawdown circuit breakers, Position limits. | Commercial: `packages/mdrap-analytics/`<br>Shim: `src/mdrap/risk.py` |
| **Algorithmic Strategy SDK** | **None** (Out of core scope). | Strategy execution templates (`Strategy`, `StrategyRunner`), simulated paper execution engine, portfolio position trackers. | Commercial: `packages/mdrap-strategies/`<br>Shim: `src/mdrap/strategy_sdk.py` |
| **Maritime Intelligence** | **None** (Out of core scope). | AIS vessel navigation tracking, geofenced chokepoint monitoring (Suez, Panama, Malacca, Hormuz), ETA forecasting. | Commercial: `packages/mdrap-contrib-vessel/`<br>Shim: `src/mdrap/vessel.py` |
| **API & Administration** | Single-node REST/WebSocket daemon (`service.py`), constant-time token authentication, local RBAC. | Enterprise multi-tenant portal, OAuth2/OIDC single sign-on, billing & token quota managers. | Core: `src/mdrap/service.py`, `src/mdrap/security.py` |

---

## 3. Boundary & Invariant Rules

To ensure both editions remain reliable, testable, and independently deployable:

1. **One-Way Dependency Invariant**:
   - `mdrap-core` **NEVER** imports, references, or requires any package from `packages/` or external commercial modules.
   - Commercial packages **MAY** import from `mdrap-core` (e.g. `from mdrap.models import CanonicalEvent`).

2. **Standardized Event Contract**:
   - All components exchange data using the immutable, slotted `CanonicalEvent` dataclass or 128-byte SBE binary frames.
   - Downstream packages must adapt to `CanonicalEvent` rather than requiring `mdrap-core` to mutate its internal layout.

3. **Backward Compatibility via Deprecation Shims**:
   - In the core repository, modules that were transitioned to companion packages (`options.py`, `tca.py`, `risk.py`, `strategy_sdk.py`, `vessel.py`) contain backward-compatible shims.
   - When a caller imports `from mdrap.options import ...`, a `DeprecationWarning` is emitted, informing the developer to migrate to the standalone package (`mdrap_options`).

---

## 4. Maintainer's Synchronization Guide

When updating either the free core or commercial packages, maintainers must follow this maintenance protocol to keep both editions synchronized and avoid breakage.

### Scenario A: Modifying Core Data Models (`models.py`)

If you add or modify fields in `CanonicalEvent`, `RawEvent`, or `EventType`:

1. **Audit Binary Frame Offsets**:
   - Check if the change impacts SBE wire framing in `src/mdrap/sbe.py` or SHM ring buffer layout in `src/mdrap/shm.py`.
   - Maintain the **128-byte cache-line alignment rule** (2 x 64-byte L1 cache lines). If adding fields to binary structures, adjust padding bytes accordingly.
2. **Update Native C Mirrors**:
   - Update `FastEvent` and `SbeTickPayload` structs in `src/mdrap/fastpath.c` and `src/mdrap/mdrap_core.c`.
   - Run `python fuzz/fuzz_differential.py --iterations 5000` to verify that Python and C decoders agree on all fields.
3. **Verify Commercial Ingestion**:
   - Ensure `mdrap-analytics` and `mdrap-options` continue to access expected fields without `AttributeError`.
   - Run `pytest tests/test_companion_packages.py -v`.

### Scenario B: Adding or Updating Quality Rules (`rules.def` & `quality.py`)

1. **Update `rules.def`**:
   - `rules.def` is the single source of truth for all reason codes and bitmask allocations.
   - Add new rules with explicit bitmask positions (`1 << N`).
2. **Regenerate Code**:
   - Run `python tools/gen_reasons.py` to regenerate both Python `Reason` enum in `src/mdrap/rules.py` and C header defines in `src/mdrap/fastpath.c`.
3. **Parity Testing**:
   - Add differential test cases to `tests/test_phase5_event_type_parity.py`.
   - Run tests with both native C enabled and pure-Python fallback (`$env:MDRAP_DISABLE_FASTPATH="1"`).

### Scenario C: Updating Commercial / Companion Modules

1. **Work in `packages/` or Private Repos**:
   - Make all feature changes within the standalone companion directories (`packages/mdrap-options`, `packages/mdrap-analytics`, etc.).
2. **Verify Against `mdrap-core` Minimum Version**:
   - In the companion's `pyproject.toml`, declare the compatible core dependency range:
     ```toml
     dependencies = ["mdrap-core>=3.1.0"]
     ```
3. **Never Add Commercial Logic to `src/mdrap/`**:
   - Keep `src/mdrap/options.py`, `src/mdrap/tca.py`, etc., strictly as non-intrusive import shims.

### Scenario D: Release & Public Git Hygiene

Before performing any release or pushing to a public repository:

1. **Verify `.gitignore`**:
   - Ensure `packages/` is listed in `.gitignore` so proprietary companion code is never tracked in the public Git tree.
2. **Run Release Inspection**:
   - Run `python -m pytest tests/test_phase8_edition_boundaries.py tests/test_phase10_ci_and_release.py -v`.
   - Verify that `mdrap-core` builds cleanly without companion packages:
     ```bash
     python -m build --sdist --wheel --no-isolation
     ```
3. **Inspect Generated Wheel**:
   - Unpack or inspect `dist/*.whl` to confirm it contains strictly `src/mdrap/` files and zero commercial packages.

---

## 5. Quick Reference for Developers

### Installing Free / Core Engine
```bash
# Clone the open-source repository
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap

# Install core in editable mode
python -m pip install -e .

# Optional: compile C acceleration
python build_fastpath.py
```

### Installing Commercial / Companion Packages (Internal Developers / Clients)
```bash
# From private source directory:
python -m pip install -e packages/mdrap-options
python -m pip install -e packages/mdrap-analytics
python -m pip install -e packages/mdrap-strategies
python -m pip install -e packages/mdrap-contrib-vessel
```

### Verifying Full Platform Health
```bash
# Core verification (Free)
pytest tests/test_phase*.py -v

# Companion verification (if present locally)
pytest tests/test_companion_packages.py -v
```
