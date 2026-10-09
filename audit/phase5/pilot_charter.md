# MDRAP Phase 5 — Controlled Production Pilot Charter

**Platform**: Market Data Reliability & Acceleration Platform (MDRAP v3.0.0)  
**Pilot Name**: Pilot Alfa-1 (Deterministic Replay & Independent Consumer Pilot)  
**Effective Date**: 2026-10-09  
**Pilot Owner**: Principal Systems & SRE Lead  
**Classification**: Controlled Pre-Production Pilot  

---

## 1. Pilot Mission & Business Objective

To evaluate MDRAP's operational deployment, telemetry exposition, consumer onboarding, and incident response under sustained workload conditions prior to connecting external institutional capital or live proprietary trading desks.

---

## 2. In-Scope vs Excluded Boundaries

### In-Scope
- **Deployment Topology**: Profile A (Single-Node High Throughput with IngestLog WAL).
- **Authorized Sources**: Simulated deterministic exchange feeds (`REPLAY_NASDAQ`, `REPLAY_ARCA`).
- **Authorized Consumers**: Independent Quant Consumer application via C++17 and Python SDKs.
- **Event Types**: Normalized `TRADE` and `QUOTE` events for US Equities (`AAPL`, `MSFT`, `NVDA`, `SPY`).
- **Target Throughput**: 10,000 to 25,000 events/second sustained.
- **Accounting & Licensing**: Tenant `Desk_Algo_One` with SHA-256 salted tokens and SQLite WAL durable metering.
- **Monitoring**: Prometheus `/metrics` pulling drop counts, quality distributions, and sequence gaps.

### Explicitly Excluded
- Real commercial feeds with financial liabilities (no live NASDAQ/OPRA credentials).
- Live trade execution or order routing to broker gateways.
- Cross-region multi-site clustering (Profile B failover tested locally only).
- AF_XDP kernel-bypass hardware networking.

---

## 3. Success & Abort Criteria

### Pilot Success Criteria
1. **Zero Unaccounted Data Loss**: Total Ingested Events == Processed Events.
2. **Quality Priority**: Strict `INVALID > SUSPICIOUS > VALID` ordering maintained 100%.
3. **Tail Latency**: Median latency <= 100 µs; p99 latency <= 500 µs under sustained load.
4. **Zero Memory Leakage**: Heap delta remains <= 5 MB over entire run duration.
5. **Idempotent Accounting**: Zero duplicate charges recorded for replayed event IDs.

### Immediate Abort Triggers
1. Unhandled exception in the ingestion hot path causing process exit without WAL sync.
2. Undetected sequence gap emitted to downstream consumer.
3. Plaintext secret leak in logs or diagnostic bundles.
4. Unbounded queue memory expansion (>256 MB heap).
