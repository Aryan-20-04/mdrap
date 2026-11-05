<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo_dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/logo_light.svg">
  <img src="assets/logo.svg" alt="MDRAP Logo" width="130">
</picture>

# MDRAP

### Market Data Reliability & Acceleration Platform

A deterministic, high-throughput market data validation, normalization, and cryptographic audit sidecar for quantitative trading systems.

[![Version](https://img.shields.io/badge/version-4.0.1-blue.svg?style=flat-square)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-1700%2B%20passing-10b981?style=flat-square)](tests/)
[![License: MIT](https://img.shields.io/badge/license-MIT-8b5cf6?style=flat-square)](LICENSE)
[![Language](https://img.shields.io/badge/language-Python%20%7C%20C%20(AVX2)-6366f1?style=flat-square)](src/mdrap/fastpath.c)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-334155?style=flat-square)](#installation)

[**Quickstart**](#quickstart--demo) &nbsp;•&nbsp; [**Architecture**](docs/architecture.md) &nbsp;•&nbsp; [**Editions & Boundaries**](EDITIONS.md) &nbsp;•&nbsp; [**Benchmarks**](#performance-benchmarks-v53) &nbsp;•&nbsp; [**Audit Certification**](audit/v5.3/RELEASE_CERTIFICATION.md) &nbsp;•&nbsp; [**Documentation**](docs/)

</div>

---

## Why MDRAP?

| Core Capability | Architectural & Quantitative Advantage |
| :--- | :--- |
| **Crossed Quote & Anomaly Defense** | Eliminates phantom arbitrage losses by intercepting crossed books (`bid > ask`), sequence jumps, negative prices, and rolling Welford price corridor anomalies before downstream strategy execution. |
| **Microsecond-to-Nanosecond Tiers** | Standalone native C kernel evaluates ticks at **45 ns/tick** (22.2M EPS); native C fastpath FFI processes at **510.8k EPS** (1.6 µs p50); pure Python fallback runs at **170.1k EPS** with zero mandatory dependencies. |
| **Institutional Microstructure & EMS** | Discrete-event matching engine with price-time priority, queue tracking, partial fills, cancellation races, Avellaneda–Stoikov HFT MM, and 14 pre-trade risk gates. |
| **Denomination-Aware Basis Valuation** | Rejects unexecutable cross-venue arbitrage claims by normalizing quotes across USD/USDT basis differences and accounting for bilateral fee hurdles. |
| **Zero Silent Drops** | Corrupted, malformed, or delayed frames are safely quarantined in a queryable SQLite ledger with explicit bitmask reason codes and complete upstream provenance. |
| **Tamper-Evident Merkle Proofs** | SHA-256 Merkle chain guarantees mathematical auditability: modifying any historical database record immediately invalidates the cryptographic integrity proof. |

---

## Architecture Pipeline

```mermaid
flowchart LR
    subgraph Ingress ["1. Ingress & Normalize"]
        A["Raw Feeds\n(Binance, ITCH, SBE, CME)"] --> B["Gateway Normalization\n(RawEvent)"]
    end
    subgraph Core ["2. Reliability & Fastpath"]
        B --> C{"Quality Engine\n(15 Rules)"}
        C -->|Valid / Suspicious| D["Multi-Feed Reconciler\n(CanonicalEvent)"]
        C -->|Invalid| Q["Quarantine Ledger\n(Reason Bitmask)"]
    end
    subgraph Handoff ["3. Handoff & Durability"]
        D --> E["IngestLog WAL\n(CRC-32C Crash Durability)"]
        D --> F["SPMC Ring Buffer\n(Zero-Copy SHM v3 <1µs)"]
        E --> G["SQLite / DuckDB\n(Projections & Audit)"]
    end
```

---

## Quickstart & Demo

<p align="center">
  <img src="tools/demo/mdrap_demo.gif" alt="MDRAP Real-Time Market Data Anomaly Detection & Audit Demo" width="92%">
</p>

### 5-Minute Golden Path

<!-- doc-test: python -->
```python
# doc-test
import tempfile, time
from mdrap import Engine, EngineConfig, RawEvent

wal_dir = tempfile.mkdtemp()
engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

now = time.time()
raw = RawEvent(
    source="BATS",
    payload={
        "instrument": "AAPL",
        "event_type": "QUOTE",
        "bid": 150.25,
        "ask": 150.30,
        "exchange_ts": now,
        "sequence": 1,
    },
    receive_timestamp=now,
)

decision = engine.submit(raw)
event = decision.canonical_event

print(f"Status: {event.quality_status.name}")
print(f"BBO: {event.bid_price} / {event.ask_price}")
ticks = engine.query("AAPL", limit=5)
print(f"Queried ticks: {len(ticks)}")
engine.close()
```

Expected output:
```text
Status: VALID
BBO: 150.25 / 150.3
Queried ticks: 1
```

---

## Installation

<details open>
<summary><b>Linux / macOS (pip)</b></summary>

```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
pip install -e .

# Verify installation & active acceleration tier
python cli.py doctor
```
</details>

<details>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap
pip install -e .

# Run diagnostics
python cli.py doctor
```
</details>

<details>
<summary><b>Docker</b></summary>

```bash
docker build -t mdrap:latest .
docker run --rm -it -p 8000:8000 mdrap:latest mdrap doctor
```
</details>

---

## Performance Benchmarks (v5.3)

All figures are measured on committed benchmark hardware (Intel Core i7-12700H, 12 cores, Windows 11 Enterprise AMD64 / Linux parity) and trace directly to committed JSON outputs in [`audit/v5.3/raw/`](audit/v5.3/raw/).

| Stage / Tier | Scope / Conditions | p50 Latency | p99 Latency | Throughput (EPS) | Reproduce Command |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Isolated Native C Kernel** | Peak tick evaluation & SBE decode (`fastpath.c`) | 22.9 ns | 45.0 ns | **43,673,843** | `python benchmarks/bench_scale.py` |
| **Native C Fastpath FFI** | Python C-FFI validation (`FastQualityEngine`) | 1.60 µs | 4.10 µs | **510,820** | `python benchmarks/run_v53_unified_benchmark.py` |
| **Pure Python Fallback Parity** | In-memory normalization, 15 rules, dedup | 8.3 µs | 18.00 µs | **170,080** | `python benchmarks/run_v53_unified_benchmark.py` |
| **Shared Memory IPC Fanout** | 10 subscribers, zero-copy seqlock handoff | 0.40 µs | 0.60 µs | **1,529,870** | `python benchmarks/run_honest_benchmarks.py` |
| **Matching Engine EMS** | Discrete-event price-time limit order match | 3.80 µs | 11.20 µs | **263,158** | `python benchmarks/run_v53_unified_benchmark.py` |
| **14 Pre-Trade Risk Gates** | Pre-trade risk rule evaluations | 1.20 µs | 3.50 µs | **833,333** | `python benchmarks/run_v53_unified_benchmark.py` |
| **Stateful E2E Pipeline** | Ingestion → Validation → Reconciler → WAL | 41.00 µs | 110.00 µs | **21,850** | `python benchmarks/run_v53_unified_benchmark.py` |
| **Multicore Stateful Pipeline** | 8 concurrent worker processes | — | — | **82,100** | `python benchmarks/run_v53_unified_benchmark.py` |
| **Disk Durability (Grouped)** | SQLite WAL, `fsync="grouped_by_time"` (batch 200) | 6.82 ms | 6.91 ms | **29,654** | `python benchmarks/run_honest_benchmarks.py` |
| **Disk Durability (Always)** | SQLite WAL, `fsync="always"` (batch 100) | 4.60 ms | 4.84 ms | **21,615** | `python benchmarks/run_honest_benchmarks.py` |

> [!NOTE]
> **Understanding Throughput Tiers**: MDRAP's native C kernel operates at **>22M EPS** (sub-50ns) when performing isolated in-memory rule validation. In Python, the native C fastpath FFI processes **510.8k EPS** (1.6 µs p50), representing a **3.00x** speedup over the pure-Python fallback (170.1k EPS). Persisting every event durably to disk via synchronous SQLite WAL fsync achieves **21k–30k EPS**, scaling to **82.1k EPS** across 8 worker processes.

---

## Deep Dive & Documentation

<details>
<summary><b>Feature Status Matrix</b></summary>

| Feature | Status | Docs | Test File |
| :--- | :--- | :--- | :--- |
| **Deterministic Quality Engine (15 Rules)** | **Stable** | [Quality Rules](docs/reference/rules.md) | `tests/test_adapters_and_rules.py` (1,500+ core test suite passed cleanly) |
| **Native C Fastpath Kernel** | **Stable** | [Benchmarks](docs/benchmarks.md) | `tests/test_fastpath.py` (100% pure Python fallback parity) |
| **Institutional Matching Engine & EMS** | **Stable** | [Architecture](docs/architecture.md) | `tests/test_hft_matching_engine.py` |
| **Avellaneda-Stoikov & Linear Skew MM** | **Stable** | [Architecture](docs/architecture.md) | `tests/test_v53_quantitative_remediation.py` |
| **14 Pre-Trade Risk Gates & Kill Switch** | **Stable** | [Architecture](docs/architecture.md) | `tests/test_v53_quantitative_remediation.py` |
| **Live Venue Ingestion (5 Feeds)** | **Stable** | [Operations](docs/guides/operations.md) | `tests/test_ws_live_market_data.py` |
| **Append-Only IngestLog WAL (CRC-32C)** | **Stable** | [Event Schema](docs/reference/event-schema.md) | `tests/test_wal_format_v2.py` |
| **Single-Node Canonical Engine API** | **Stable** | [SDK Integration](docs/guides/sdk-integration.md) | `tests/test_engine.py` |
| **Cryptographic Merkle Audit Verification** | **Stable** | [Audit Verification](docs/guides/audit-verification.md) | `tests/test_audit_remediation.py` |
| **FastAPI REST & WebSocket Server** | **Stable** | [API Reference](docs/reference/api.md) | `tests/test_api_endpoints.py` |
| **Shared Memory SPMC Ring Buffer (SHM v3)**| **Stable** | [Architecture](docs/architecture.md) | `tests/test_shm_decoupled.py` |
| **ITCH, SBE & Wire Protocol Dissectors** | **Stable** | [Feed Adapters](docs/extending/feed-adapter.md) | `tests/test_sbe.py` |
| **Streaming WAL Journal Replication** | **Beta** | [Operations](docs/guides/operations.md) | `tests/test_journal_replication_daemon.py` |
| **AF_XDP Kernel Bypass Driver Blueprint** | **Experimental** | [Roadmap](docs/roadmap.md) | `tests/test_af_xdp.py` |
| **Distributed Multi-Host Raft Consensus** | **Roadmap** | [Roadmap](docs/roadmap.md) | Planned (Mode B) |

</details>

<details>
<summary><b>Pick Your Path (By Persona)</b></summary>

| Persona | 3-Line Example | Guide |
| :--- | :--- | :--- |
| **Quant Researcher** | `from mdrap.chd import CHDClient, HistoricalRequest`<br>`client = CHDClient(cache_dir="./data/cache")`<br>`ticks = client.download(HistoricalRequest("binance", "spot", "BTCUSDT", "trades", "2026-01-01"))` | [Research & Backtesting Guide](docs/guides/research-backtesting.md) |
| **Trader / PM** | `from mdrap.service import StreamClient`<br>`client = StreamClient(host="127.0.0.1", port=9988)`<br>`for tick in client.subscribe(["NVDA"]): print(tick)` | [Live Monitoring Guide](docs/guides/live-monitoring.md) |
| **Developer** | `engine = Engine.open("./wal", config=EngineConfig(db_path=":memory:"))`<br>`decision = engine.submit(raw_event)`<br>`ticks = engine.query("AAPL", limit=10)` | [SDK Integration Guide](docs/guides/sdk-integration.md) |
| **SRE / DevOps** | `curl -f http://127.0.0.1:8000/v1/health`<br>`curl -f http://127.0.0.1:8000/metrics`<br>`mdrap doctor --json` | [Operations Guide](docs/guides/operations.md) |
| **Compliance / Risk** | `from mdrap.storage import Store`<br>`store = Store("data/mdrap.db")`<br>`report = store.verify_audit_integrity()` | [Audit Verification Guide](docs/guides/audit-verification.md) |

</details>

<details>
<summary><b>Scope & Known Limitations</b></summary>

- **Scope Boundary**: MDRAP is a single-node middleware sidecar. It does not provide proprietary feed licenses (bring your own API keys) and does not execute broker orders.
- **Edition Boundaries**: MDRAP is structured under an uncompromising Open-Core model. The core platform (`mdrap-core`) is 100% MIT-licensed with zero mandatory external dependencies. Specialized institutional packages (Options Pricing, TCA, Portfolio Risk, Algorithmic Strategies) are isolated as downstream companion packages documented in [EDITIONS.md](EDITIONS.md).
- **Feed Runtime**: Registration exposes metadata only. There is no supervisor that owns adapter lifecycle or background process spawning.
- **Failover**: Local active-passive replication via TCP journal streaming (`journal_replicator.py`); multi-datacenter distributed Raft consensus is on the roadmap.
- **Backpressure**: When downstream consumers cannot keep up with high-frequency streams, oldest frames are evicted to prioritize real-time low latency while updating drop counters.
- **Compliance Tooling**: Emits audit trails that can support SEC Rule 613 (CAT) or MiFID II RTS 25 compliance workflows, but software execution does not by itself confer legal certification.

</details>

<details>
<summary><b>Project Structure</b></summary>

```text
mdrap/
├── src/mdrap/              # Core market data engine package
│   ├── engine.py           # Canonical engine API & step orchestration
│   ├── gateway.py          # Multi-venue normalization (RawEvent -> CanonicalEvent)
│   ├── quality.py          # 15 deterministic microstructure quality checks
│   ├── reconciliation.py   # Multi-feed arbitration and provenance tracking
│   ├── ingestlog.py        # CRC-32 write-ahead log (WAL) for crash durability
│   ├── shm.py              # Zero-copy SPSC shared-memory ring buffer (<1µs)
│   ├── fastpath.c          # Native C hot-path kernel (AVX2/SIMD + LTO)
│   ├── bbo.py              # Consolidated NBBO and micro-price tracking
│   ├── depth.py            # Level-2 aggregated order book depth ladder
│   └── security.py         # SHA-256 Merkle audit trail and API key hashing
├── tests/                  # 1,700+ automated unit & integration test suites
├── benchmarks/             # Reproducible latency and throughput harnesses
├── docs/                   # Architectural guides, API specs, and runbooks
├── audit/v5.3/             # Independent v5.3 engineering audit & certification
├── tools/                  # Verification guardrails (check_docs.py, VHS demo)
└── assets/                 # Vector SVG logos and brand assets
```

</details>

---

## Documentation Index

- **Getting Started**: [Quickstart Guide](docs/quickstart.md) · [Concepts & Data Model](docs/concepts.md)
- **User Guides**: [Research & Backtesting](docs/guides/research-backtesting.md) · [Live Monitoring](docs/guides/live-monitoring.md) · [SDK Integration](docs/guides/sdk-integration.md) · [Operations Guide](docs/guides/operations.md) · [Audit Verification](docs/guides/audit-verification.md)
- **Technical Reference**: [CLI Reference](docs/reference/cli.md) · [REST & WebSocket API](docs/reference/api.md) · [Configuration Options](docs/reference/config.md) · [Quality Rules Catalog](docs/reference/rules.md) · [Event Schema](docs/reference/event-schema.md)
- **Platform Specs**: [Platform Editions & Maintenance Guide](EDITIONS.md) · [v5.3 Release Certification](audit/v5.3/RELEASE_CERTIFICATION.md) · [Security Architecture](docs/security.md) · [Benchmark Methodology](docs/benchmarks.md) · [Operational Runbooks](docs/operations.md) · [Architecture Roadmap](docs/roadmap.md)

---

## Contributing, Security & License

- **Contributing**: Issues and pull requests are welcomed! See [DEVELOPMENT.md](docs/development/baseline.md) for code styling and test verification gates.
- **Security**: Report vulnerabilities according to our [Security Policy](SECURITY.md).
- **License**: Released under the [MIT License](LICENSE). Copyright (c) 2026 MDRAP Authors.

<p align="center">
  Developed and maintained by the MDRAP Authors.
</p>
