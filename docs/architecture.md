# MDRAP Architecture Specification

Based on the *Market Data Reliability & Acceleration Platform Reference (Section 5, 6, 14, 26)*.

## 1. System Overview

The platform converts noisy, delayed, duplicated, and inconsistent market data from multiple sources into a fast, validated, canonical real-time data stream with end-to-end lineage and measurable reliability.

```
+-------------------------------------------------------------+
|                Feed Simulator / External Feeds              |
|        (Deterministic generators, synthetic error injection)|
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                        Feed Gateway                         |
|   - Ingest: receive timestamp, unique raw_id                |
|   - Normalize: vendor format -> CanonicalEvent schema       |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                    Data-Quality Engine                      |
|   - Schema validation (never drop, quarantine INVALID)      |
|   - Dedup (bounded LRU key cache)                           |
|   - Sequence-gap & out-of-order detection                   |
|   - Staleness & price anomaly (Welford's z-score)           |
|   - Quote consistency (crossed books)                       |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                 Cross-Feed Reconciler                       |
|   - Aligns observations by simulated market time            |
|   - Tracks dynamic source reliability scores                |
|   - Resolves conflicts & records decision reasons           |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                        Storage Sink                         |
|   - SQLite (V1 baseline, executemany batched writes)        |
|   - Segregated: canonical_events, quarantine, lineage,      |
|     source_health                                           |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|               Observability & Query Layer                   |
|   - Rich terminal dashboard (refresh throttled @ 8 Hz)      |
|   - CLI queries: latest quote, lineage, health, quarantine  |
|   - Structured JSON benchmark & loadtest outputs            |
+-------------------------------------------------------------+
```

---

## 2. Component Responsibilities

### 2.1 Feed Gateway (`src/gateway.py`)
- **`ingest()`**: Records high-resolution `receive_timestamp` immediately upon arrival and assigns a monotonic `raw_id`.
- **`normalize()`**: Maps vendor-specific schemas (e.g., Feed A vs Feed B payloads) to the uniform `CanonicalEvent`. Catches `SchemaError` and packages it into an `INVALID` event for quarantine.

### 2.2 Quality Engine (`src/quality.py`)
- Stateful per-`(source, instrument)` evaluation.
- Classifies into three non-downgradable levels: `VALID` < `SUSPICIOUS` < `INVALID`.
- Uses Welford's algorithm (`_RollingStats`) for numerically stable rolling mean and standard deviation.
- Deduplication uses an insertion-ordered LRU dict.

### 2.3 Cross-Feed Reconciler (`src/reconciliation.py`)
- Maintains per-instrument alignment across multiple feeds.
- Computes real-time source reliability scores based on weighted performance:
  - Accuracy / Agreement: 40%
  - Completeness (no sequence gaps): 25%
  - Deduplication cleanliness: 20%
  - Latency / Freshness: 15%
- When observations conflict, picks the source with highest reliability and records the rationale in the lineage record.

### 2.4 Storage & Quarantine (`src/storage.py`)
- Segregates data streams into distinct tables:
  - `canonical_events`: Only `VALID` and `SUSPICIOUS` market events.
  - `quarantine`: `INVALID` events with raw payload and failure reason.
  - `lineage`: Step-by-step traceability linking canonical events to raw IDs and decision rules.
  - `source_health`: Historical log of source reliability scores.
- Context-manager enabled with batched `executemany` commits to prevent transaction overhead.

### 2.5 Observability & Metrics (`src/metrics.py`, `src/dashboard.py`)
- Tail latency calculation (p50, p95, p99, p99.9).
- Bounded rolling sample window for real-time dashboard percentiles to prevent O(N log N) sorting overhead.
- Final summary sorts the entire run for precision.

---

## 3. Evolutionary Roadmap (Versions 1 - 4)

- **V1 (Current Baseline):** Single-process, synchronous Python pipeline. SQLite storage with batched writes. Establishes the ground-truth benchmark and profiling baseline.
- **V2 (Streaming Architecture):** Decouple ingestion and processing via Kafka / Redpanda. Replace SQLite with PostgreSQL for canonical storage.
- **V3 (Analytical Scale):** High-throughput analytical storage with ClickHouse for tick history, order-book snapshots, and deep lineage querying.
- **V4 (Ultra-Low Latency):** C++ hot path for gateway normalization and quality validation, with Python bindings / consumers for downstream analytics.
