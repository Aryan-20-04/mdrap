# MDRAP Documentation Index

Welcome to the Market Data Reliability & Acceleration Platform (MDRAP) documentation. MDRAP is an institutional market-data validation, normalization, cross-feed reconciliation, and cryptographic audit sidecar designed to sit between upstream market feeds and downstream trading strategies, analytics engines, and tick databases.

This documentation suite is organized by stakeholder audience to provide direct access to the relevant guides, specifications, and reference materials.

---

## 1. Quants & Quantitative Traders

Guides and specifications for market microstructure, analytical modeling, anomaly detection, and strategy backtesting:

- **[Microstructure & Data Model](data-model.md)**: Canonical event schemas (`TRADE`, `QUOTE`, `BBO`, `L2_UPDATE`), field types, timestamp semantics, and sequence ordering.
- **[Quality Rules & Anomaly Detection](quality-rules.md)**: The 24 financial quality checks, Welford numerical variance filtering, dynamic volatility bands, and non-downgradable quality states (`INVALID > SUSPICIOUS > VALID`).
- **[Order Flow & Aggressor Side](flow.md)**: Institutional order flow tracking, Lee-Ready aggressor classification, Cumulative Volume Delta (CVD), and participant delta tracking.
- **[Research, TCA & Trading Analytics](RESEARCH_AND_TRADING.md)**: Transaction Cost Analysis (TCA), Implementation Shortfall, VWAP/TWAP slippage models, and options pricing engines.
- **[CLI Reference for Traders](cli-reference.md)**: Interactive terminal trading desk (`mdrap desk`), live ASCII/Unicode candlestick charts (`mdrap chart`), L2 order book depth (`mdrap depth`), and synthetic NBBO queries (`mdrap bbo`).

---

## 2. Platform & Infrastructure Engineers

Guides for system operators, SREs, and DevOps deploying and operating MDRAP in institutional production environments:

- **[Installation & Build Guide](installation.md)**: Source checkouts, package realities, optional extras (`.[api]`, `.[ui]`), native C hot-path compilation, and Docker containerization.
- **[Configuration Catalog](configuration.md)**: Complete catalog of environment variables, defaults, and the hierarchical `mdrap.toml` configuration ladder.
- **[Architecture & Engine Pipeline](architecture.md)**: Multi-tier processing topology, zero-lock SPSC shared memory ring buffer (`shm.py`), WAL durability guarantees, and asynchronous network fanout.
- **[CLI Reference Manual](cli-reference.md)**: Authoritative reference for all 69 commands and aliases generated directly from live `argparse`.
- **[Deployment & High Availability](deployment.md)**: Multi-node deployment topology, lease consensus coordination, epoch-fenced storage boundaries, and sub-110ms failover lifecycles.
- **[Chaos Drills & Resilience Testing](chaos-drills.md)**: Automated fault injection, simulated network partitions, abrupt process terminations, and disk exhaustion recovery.
- **[Benchmarking & Performance Methodology](benchmark-methodology.md)**: Reproducible latency profiling, p50/p95/p99/p99.9 tail measurement, AVX2 SIMD kernel validation, and throughput benchmarks.
- **[Backup & Disaster Recovery](backup-restore.md)**: Cold backups, WAL snapshot restoration, and point-in-time state reconstruction.

---

## 3. Application & Integration Developers

Guides for software engineers integrating downstream trading systems or building custom exchange feed adapters:

- **[Developer Quickstart](quickstart.md)**: Step-by-step walkthrough to initialize `Engine.open()`, submit `RawEvent` objects, and query canonical ticks in Python.
- **[REST & WebSocket API Reference](api.md)**: Real route table with verified `curl` examples, RBAC authentication headers, health probes, and event streams.
- **[Python SDK Guide](sdk.md)**: High-level Python client library (`from mdrap import Engine, Client, RawEvent`), connection pooling, and asynchronous streaming.
- **[Feed Adapter Protocols](extending/)**: Interface specification for implementing custom venue connectors and normalizers.
- **[API Stability Policy](API_STABILITY.md)**: Semantic versioning guarantees, deprecation cycles, and interface contract commitments.

---

## 4. Risk, Compliance & Audit Officers

Resources for quantitative risk managers, regulatory compliance teams, and external auditors:

- **[Cryptographic Audit Trail Format](audit-log-format.md)**: SHA-256 Merkle chain specifications, periodic HMAC-SHA256 signed checkpoints, and tamper-evident lineage proofs.
- **[Platform Operational Status](status.md)**: Comprehensive, auditable breakdown of what is verified (Phases 0–11, Mode A & Mode B), what remains gated (Mode C & Mode D), and known architectural boundaries.
- **[Verified Gap Report](verified-gap-report.md)**: Objective verification matrix across all platform stability cards, test assertions, and audit deliverables.
- **[Security Policy & Disclosure](../SECURITY.md)**: Supported versions table, reporting procedures, and cryptographic integrity standards.
- **[Data Licensing Notice](data-licensing.md)**: Clear separation between MDRAP platform software and customer direct exchange market data licensing obligations.
