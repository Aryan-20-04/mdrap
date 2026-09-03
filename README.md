# Market Data Reliability & Acceleration Platform (MDRAP)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-93%2F93%20passing-brightgreen.svg)](tests/)
[![Hot Path Latency](https://img.shields.io/badge/hot--path-89.2%20ns-orange.svg)](src/fastpath.c)
[![Architecture](https://img.shields.io/badge/architecture-V1%20%7C%20V2%20%7C%20V4-purple.svg)](docs/architecture.md)
[![Dependencies](https://img.shields.io/badge/dependencies-zero%20mandatory-success.svg)](requirements.txt)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A financial-market infrastructure platform designed to convert noisy, delayed, duplicated, and inconsistent market data from multiple public and private sources into a fast, validated, canonical real-time data stream with measurable reliability, explainable lineage, and cryptographic auditability.

---

## High-Level Architecture

```mermaid
flowchart TD
    subgraph INGESTION ["Market Ingestion Layer"]
        B[Binance Public WebSocket/REST]
        C[Coinbase Public REST]
        S[Deterministic Feed Simulator<br/>FEEDX, FEEDY, FEEDZ]
    end

    subgraph SECURITY ["Security & Gatekeeper (Spec §19)"]
        RL[Token Bucket Rate Limiter<br/>20,000 eps]
        SG[Regex & Range Input Sanitizer]
        HMAC[HMAC-SHA256 Payload Verification<br/>Constant-Time Digest]
    end

    subgraph PIPELINE ["Validation & Consensus Pipeline"]
        GW[Gateway & Normalization]
        QE[7-Rule Quality Engine<br/>Schema, Dedup, Gap, Order, Stale, Crossed, Anomaly]
        FP[Native C Hot Path Accelerator<br/>GCC -O3 | 89.2 ns/event]
        RECON[Cross-Feed Reconciler & Consensus]
        WD[Source Watchdog & Failover Circuit Breaker]
        BBO[Synthetic Consolidated BBO<br/>Multi-Venue NBBO]
    end

    subgraph STORAGE ["Batched Storage & Audit (SQLite)"]
        CAN[canonical_events]
        QUAR[quarantine<br/>Never Silently Drop]
        LIN[lineage<br/>Full Transformation Proof]
        AUD[audit_log<br/>Merkle Hash Chained]
    end

    subgraph SERVICE ["Terminal Service & IPC (Spec §18)"]
        DAEMON[Headless Streaming Daemon<br/>127.0.0.1:9876]
        SUB[Composable Stream Subscriber<br/>mdrap sub SYM --json]
        TOP[Live Terminal Cockpit<br/>mdrap top]
    end

    B --> RL
    C --> RL
    S --> RL
    RL --> SG --> HMAC --> GW --> QE
    QE <--> FP
    QE --> RECON --> BBO
    RECON --> WD
    BBO --> DAEMON
    RECON --> STORAGE
    DAEMON --> SUB
    DAEMON --> TOP
```

---

## Key Platform Capabilities

### 1. 7-Rule Data Quality Engine (Spec §7)
- **Structural Schema Validation**: Rejects malformed JSON and missing sequence/timestamp attributes.
- **Deduplication**: Identifies exact and sliding-window duplicate packet bursts.
- **Monotonic Sequence Gap Detection**: Detects missing exchange packets and penalizes feed reputation.
- **Out-of-Order Sequencing**: Catches retrograde arrival events.
- **Timestamp Staleness Evaluation**: Flags lagging feeds exceeding max latency thresholds.
- **Crossed Quote Detection**: Flags invalid book states where $\text{Bid} > \text{Ask}$.
- **Statistical Price Sanity Checks**: Evaluates sudden price jumps ($>3\sigma$) against a rolling instrument baseline.

### 2. Multi-Venue Consolidated BBO (Synthetic NBBO)
- Aggregates top-of-book across disparate venues (e.g. Binance + Coinbase + Feed Simulator).
- Computes global tightest bid/ask spread and mid-price.
- Real-time venue attribution and locked/crossed market status flags.
- Watchdog-driven source eviction: automatically prunes stale or degraded quotes.

### 3. Native C Hot-Path Accelerator (`fastpath.c`)
- Pure C implementation compiled with GCC `-O3` into a native shared library (`fastpath.dll`).
- Direct batch evaluation throughput of **11.2 million events/second (89.2 nanoseconds/event)**.
- Full parity with pure-Python quality engine, falling back seamlessly if C DLL is unavailable.

### 4. Enterprise Security & Cryptographic Audit (Spec §19)
- **HMAC-SHA256 Feed Authentication**: Anti-spoofing signature verification with pre-shared feed secrets.
- **Role-Based Access Control (RBAC)**: Enforces privilege boundaries across `VIEWER`, `OPERATOR`, and `ADMIN`.
- **Token Bucket Rate Limiting**: Shields pipeline against denial-of-service and quote flooding ($20,000\text{ eps}$).
- **Tamper-Evident Merkle Audit Log**: Cryptographically chained SHA-256 hash trail in SQLite. Run `mdrap audit --verify` to verify integrity from genesis.

### 5. Automated Failure & Chaos Engine (Spec §15)
- Automated resilience scorecard testing:
  1. Mid-stream source kill & automated watchdog failover.
  2. Network timestamp jitter & staleness quarantine.
  3. Packet burst duplicate filtering.
  4. Storage outage & memory recovery with zero data loss.

### 6. Headless Service Daemon & IPC Streaming (Spec §18)
- Designed around the **Unix philosophy**: headless background daemon, composable CLI streams, and live terminal cockpits.
- Stream clean canonical ticks directly into algorithmic trading bots or Unix pipelines:
  ```bash
  mdrap sub BTC/USD --json | jq '{bid: .bbo.bid, ask: .bbo.ask}'
  mdrap sub ETH/USD --json | python my_arbitrage_bot.py
  ```

---

## Architectural Progression & Benchmarks

Measured on identical 10,000-event workloads (`seed=42`) with fixed ground-truth errors:

| Architecture | Throughput (eps) | Proc Latency p50 | Proc Latency Max | Design Highlight |
|---|:---:|:---:|:---:|---|
| **V1 Synchronous Baseline** | **29,402 eps** | **14.6 µs** (14,600 ns) | 255.8 µs | Pure Python, synchronous loop, SQLite batched writes |
| **V2 Decoupled Streaming** | **22,351 eps** | **15.3 µs** (15,300 ns) | 19.9 ms | Multi-threaded in-memory queue bus with backpressure |
| **V4 Native C Hot Path** | **27,274 eps** | **15.7 µs** (15,700 ns) | 265.2 µs | GCC `-O3` ctypes binding with fallback safety |
| **Native C Direct Batch** | **11,204,482 eps** | **89.2 ns** (0.089 µs) | 140.0 ns | Zero-copy batched array processing in C |

---

## Quick Start

### Installation

Clone the repository and install dev dependencies:
```bash
git clone <YOUR_REPO_URL>
cd mdrap

# MDRAP requires 0 mandatory dependencies. Optional rich styling:
pip install -r requirements.txt
```

### 1. Interactive Warm Shell (Sub-Millisecond CLI)
Run `mdrap` (or `.\mdrap.bat`) with zero arguments to enter the pre-warmed interactive shell:
```bash
.\mdrap.bat
```
```text
┌────────────────────────────────────────────────────────────┐
│ MDRAP Real-Time Interactive Shell                          │
│ Pre-warmed in-memory engine | Sub-millisecond latency      │
└────────────────────────────────────────────────────────────┘
mdrap> /status
mdrap> /bbo BTC/USD
mdrap> /live BTC/USD -l 10
mdrap> /security
mdrap> /audit --verify
mdrap> /chaos all
mdrap> /test
mdrap> /exit
```

### 2. Headless Daemon & Live IPC Streaming
In **Terminal 1**, start the background ingestion daemon:
```bash
# High-speed simulated multi-venue feed
.\mdrap.bat daemon --speed 2000

# Or live public Binance & Coinbase feeds
.\mdrap.bat daemon --live
```

In **Terminal 2**, stream live ticks to stdout (or pipe to trading bots):
```bash
# Stream formatted color ticks
.\mdrap.bat sub BTC/USD

# Stream raw JSON for algorithms or jq
.\mdrap.bat sub BTC/USD --json
```

In **Terminal 3**, launch the live terminal service cockpit:
```bash
.\mdrap.bat top
```

---

## Verification & Testing

Run the full automated test suite (93 unit, integration, security, and chaos tests):
```bash
pytest tests/ -v
```

Run the end-to-end comprehensive platform verification scorecard:
```bash
.\mdrap.bat /test
```

---

## Repository Structure

```text
mdrap/
├── cli.py               # Unified CLI, slash-command pre-processor, interactive shell
├── mdrap.bat            # Windows zero-config batch launcher
├── pyproject.toml       # PEP 518/621 project configuration & CLI packaging
├── requirements.txt     # Optional runtime & dev dependencies
├── LICENSE              # MIT License
├── src/
│   ├── models.py        # CanonicalEvent, RawEvent, QualityStatus dataclasses
│   ├── gateway.py       # Ingestion gateway and schema normalizer
│   ├── quality.py       # 7-rule data quality evaluation engine
│   ├── fastpath.c       # Native C quality accelerator (GCC -O3)
│   ├── fastpath.py      # C ctypes wrapper with pure-Python fallback
│   ├── reconciliation.py# Multi-feed cross-reconciliation & dynamic reliability
│   ├── storage.py       # Batched SQLite store (canonical, quarantine, lineage, audit)
│   ├── pipeline.py      # V1 synchronous baseline pipeline
│   ├── pipeline_v2.py   # V2 decoupled streaming pipeline with broker
│   ├── broker.py        # Thread-safe in-memory broker with backpressure
│   ├── watchdog.py      # Live source watchdog, silence detection & failover
│   ├── bbo.py           # Synthetic Consolidated BBO (NBBO) engine
│   ├── live.py          # Real-time Binance & Coinbase market connectors
│   ├── service.py       # Headless daemon, streaming socket, and terminal cockpit
│   ├── security.py      # HMAC signing, RBAC, DoS rate limiter, Merkle audit log
│   ├── chaos.py         # Automated failure & chaos drill suite (Spec §15)
│   ├── archive.py       # Immutable write-ahead JSONL archive & replay
│   ├── analytics.py     # V3 OHLCV candles, spread analysis, realized volatility
│   ├── metrics.py       # High-resolution hardware nanosecond latency telemetry
│   ├── term.py          # Auto-responsive terminal styling with stdlib fallback
│   └── simulator.py     # Deterministic feed simulator with seeded corruptions
├── tests/               # 93 automated unit & integration tests
├── benchmarks/          # Immutable benchmark runs and cProfile traces
└── docs/                # Architecture, methodology, and quality specifications
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
