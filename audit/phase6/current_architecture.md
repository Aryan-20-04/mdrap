# MDRAP Phase 6 — Current Platform Architecture Specification

## 1. Executive Summary & Architectural Overview
This document specifies the current production-certified architecture of the Market Data Reliability & Acceleration Platform (MDRAP) at entry into Phase 6. MDRAP sits upstream of trading execution engines, quantitative analytics, and regulatory ledgers, transforming noisy, multi-venue market feeds into an authoritative, validated, canonical stream.

---

## 2. Core End-to-End Pipeline Flow

```
[ Market Feeds: ITCH, BATS, SBE, WebSockets ]
                      │
                      ▼
[ Ingress & Gateway Normalization (`src/gateway.py`) ]
  - Timestamp normalization (Clock source: "EXCHANGE" vs "GATEWAY_RECV")
  - Sequence extraction & raw envelope construction
                      │
                      ▼
[ Data Quality & Rule Engine (`src/quality.py`, `rules.def`) ]
  - Price bounds, crossed quotes, zero/negative size checks
  - Welford rolling variance & dynamic anomaly detection
  - Rule priority: INVALID > SUSPICIOUS > VALID (Bitmask tagging)
  - Invalids routed to `quarantine.db`
                      │
                      ▼
[ Cross-Feed Reconciler (`src/reconciliation.py`) ]
  - Multi-source price arbitration & reliability weighting
  - Canonical provenance recording (Chosen source, disagreement flags)
                      │
                      ▼
[ Durability Boundary: IngestLog WAL (`src/ingestlog.py`) ]
  - CRC32-framed append-only binary segment files (`.seg`)
  - Grouped fsync commit policy (`fsync_policy="grouped_by_size"`)
                      │
                      ▼
[ Distribution & IPC Kernel (`src/sbe.py`, `src/shm.py`, `src/partition.py`) ]
  - Simple Binary Encoding (SBE) 64-byte streaming frames over TCP
  - Lock-free SPSC seqlock shared memory ring buffers
  - Decoupled bounded consumer fan-out with automated slow-client eviction
                      │
                      ▼
[ Persistent Projections & Compliance (`src/metering.py`, SQLite WAL) ]
  - SQLite `canonical.db` batched writes
  - End-of-Day (EOD) Unit-of-Count usage metering & Merkle audit tree sealing
```

---

## 3. Sharded Scaling Topology (`src/partition.py`)

At Phase 6, horizontal scaling is achieved via orthogonal, zero-dependency symbol sharding:
- **`SymbolPartitioner`**: Maps tickers to independent shard instances via alphabetical ranges (`A-L` vs `M-Z`) or uniform CRC32 hashing.
- **`ShardInstance`**: Autonomous worker threads managing isolated IngestLog WAL files, SQLite projections, and SBE dispatch ports.
- **`ConsumerFanoutManager`**: Manages independent bounded queues (`maxsize=5000`) per client session with automated noisy-neighbor eviction.
- **`TenantQuotaManager`**: Enforces per-tenant subscription counts and sliding-window rate limits.
- **`FleetCoordinator`**: Coordinates shard lifecycles and provides unified `/health/fleet` aggregation.
