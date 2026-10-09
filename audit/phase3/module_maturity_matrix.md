# MDRAP Phase 3 — Module Maturity Classification Matrix

**Document Identifier**: `MDRAP-MATURITY-P3-001`  
**Date**: October 9, 2026  

---

## 1. Classification Categories

- **Production-Supported**: Fully tested, benchmarked, documented, and hardened under institutional SLA.
- **Beta / Test**: Implemented and verified in continuous integration; platform/toolchain limitations documented.
- **Experimental**: Exploratory research code; no production guarantees.
- **Deprecated**: Retained for backward compatibility; scheduled for removal.

---

## 2. Comprehensive Module Classification

| Module Path | Classification | Primary Responsibility | SLA / Support Level |
|---|---|---|---|
| `src/mdrap/gateway.py` | Production-Supported | Normalization & fast event mapping | Institutional Core |
| `src/mdrap/ingestlog.py`| Production-Supported | Append-only WAL, CRC32, atomic sync | Institutional Core |
| `src/mdrap/quality.py` | Production-Supported | Data quality rules & anomaly detection | Institutional Core |
| `src/mdrap/reconciliation.py`| Production-Supported | Multi-feed consensus & canonical resolution | Institutional Core |
| `src/mdrap/storage.py` | Production-Supported | SQLite batched WAL projection | Institutional Core |
| `src/mdrap/runtime.py` | Production-Supported | Canonical runtime lifecycle FSM | Institutional Core |
| `src/mdrap/supervisor.py`| Production-Supported| Supervised worker threads with backoff | Institutional Core |
| `src/mdrap/ingress.py` | Production-Supported | FeedAdapter contract & replay engine | Institutional Core |
| `src/mdrap/metering.py`| Production-Supported | Durable usage accounting & CSV/JSON exports | Institutional Core |
| `src/mdrap/failover.py`| Production-Supported | Epoch fencing & active-passive failover | Institutional Core |
| `src/mdrap/shm.py` | Production-Supported | Zero-copy SPSC seqlock shared memory | Institutional Core |
| `src/mdrap/fastpath.c` | Production-Supported | Native C SBE decoding & memory ring buffer | Institutional Core |
| `sdk/cpp/` | Production-Supported | Native C++17/20 consumer SDK | Institutional Core |
| `sdk/java/` | Production-Supported | Native Java 17+ consumer SDK | Institutional Core |
| `sdk/rust/` | Beta / Test | Idiomatic Rust 2021 consumer SDK | Tier 2 (Host cargo absent) |
| `src/mdrap/vessel.py` | Experimental | Vessel position tracking | Non-Core / No SLA |
| `src/mdrap/options.py` | Experimental | Options pricing & Greeks | Non-Core / No SLA |
| `src/mdrap/news.py` | Experimental | News feed sentiment parsing | Non-Core / No SLA |
| `src/mdrap/risk.py` | Experimental | Portfolio risk metrics (VaR/CVaR) | Non-Core / No SLA |
| `src/mdrap/journal.py` | Deprecated | Legacy binary journal | Migration to IngestLog |
| `src/mdrap/fx.py` | Deprecated | Global FX matrix | Non-Core / Auxiliary |
| `src/mdrap/pcap.py` | Deprecated | Raw pcap packet dissection | Non-Core / Diagnostic |
