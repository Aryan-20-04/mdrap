# MDRAP Phase 3 — Product Boundary Specification

**Document Identifier**: `MDRAP-BOUND-P3-001`  
**Date**: October 9, 2026  
**Status**: APPROVED  

---

## 1. Core Platform Scope

The primary mission of MDRAP is to serve as an institutional market-data validation, normalization, reconciliation, and audit sidecar that sits between external venues and downstream trading/analytics engines.

### In Core Scope:
- Heterogeneous feed ingress (TCP, WebSocket, ITCH, Replay).
- Fast SBE decoding and schema normalization.
- In-memory data quality engine (stat rules, anomaly detection, crossed book identification).
- Cross-feed BBO and trade reconciliation.
- Durable write-ahead logging (IngestLog WAL with CRC32 integrity).
- Asynchronous queryable projections (SQLite WAL).
- Low-latency local IPC distribution (POSIX/Win32 Shared Memory ring buffers).
- Multi-language consumer SDKs (C++, Java, Rust).
- Active-Passive failover and epoch-based split-brain prevention.
- Durable usage metering and compliance reporting.

---

## 2. Non-Core and Segregated Capabilities

The following modules represent adjacent or experimental capabilities and are explicitly isolated from the core market data engine:
- `vessel.py` (Vessel tracking & supply chain analytics): **EXPERIMENTAL / NON-CORE**
- `options.py` (Black-Scholes option pricing & Greeks): **EXPERIMENTAL / NON-CORE**
- `news.py` (Financial news sentiment analysis): **EXPERIMENTAL / NON-CORE**
- `risk.py` (Portfolio VaR / CVaR calculations): **EXPERIMENTAL / NON-CORE**
- `fx.py` (FX cross-rate matrix): **DEPRECATED / AUXILIARY**
- `pcap.py` (Raw packet dissection): **DEPRECATED / DIAGNOSTIC ONLY**
- `BinaryJournal` (`journal.py`): **DEPRECATED in favor of IngestLog**
