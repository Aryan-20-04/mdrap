# System Architecture

The **Market Data Reliability & Assurance Platform (MDRAP)** is an institutional-grade, read-only sidecar engineered to monitor, validate, normalize, and reconcile market-data feeds in real time.

---

## 1. Pipeline Dataflow

```
+-------------------------------------------------------------------------------+
|                             MARKET DATA PROVIDERS                             |
|  [Coinbase WebSocket]   [Kraken WebSocket]   [Binance Stream]   [Databento/SBE]  |
+-------------------------------------------------------------------------------+
                                      |
                                      v (Raw wire messages / frames)
+-------------------------------------------------------------------------------+
|                            INGESTION & GATEWAY                                |
|  - Parse raw wire bytes                                                       |
|  - Compute SHA-256 Payload Hash                                               |
|  - Stamp Ingress Vector: (exchange_ts, receive_ts, processing_ts)             |
|  - Normalize into CanonicalEvent (OrderBook L1/L2, Trades)                    |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                        15-RULE QUALITY EVALUATION                             |
|  - InvertedSpread, SequenceGap, StaleTick, ClockDrift, PriceJump, OutOfBounds |
|  - Classify QualityStatus: VALID | SUSPICIOUS | INVALID                       |
|  - Form Lineage Hash = SHA256(EventID + RawID + WireSHA256 + QualityVerdict)  |
+-------------------------------------------------------------------------------+
          |                                                     |
          | (VALID ticks)                                       | (SUSPICIOUS / INVALID)
          v                                                     v
+----------------------------------+          +----------------------------------+
|      CANONICAL STREAM BUFFER     |          |       QUARANTINE & INCIDENTS     |
|  - Low-latency IPC / SHM Ring    |          |  - Isolate from downstream bots  |
|  - Local REST & WebSocket API    |          |  - Open DataQualityIncident      |
+----------------------------------+          +----------------------------------+
          \                                                     /
           \                                                   /
            v                                                 v
+-------------------------------------------------------------------------------+
|                       DURABLE STORAGE & PROVENANCE                            |
|  - SQLite WAL (Batch writes via executemany)                                  |
|  - EventProvenance records linked to raw payload                              |
|  - RFC 6962 Merkle Tree leaf anchoring                                        |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                    FORENSIC REPLAY & EVIDENCE AUDITING                        |
|  - Deterministic Historical Replay Engine under VirtualClock                  |
|  - Independent Standalone Evidence Verifier (zero dependencies)               |
|  - Post-Mortem Incident Export (Markdown + JSON)                              |
+-------------------------------------------------------------------------------+
```

---

## 2. Subsystems and Responsibilities

### A. Gateway & Normalizer (`mdrap.gateway`, `mdrap.models`)
- Converts exchange-specific schemas into a unified, strongly typed `CanonicalEvent`.
- Assigns monotonic `event_id` counters (`itertools.count()`) to avoid non-deterministic UUID generation in hot paths.
- Preserves raw payload hashes (`compute_payload_hash`) before normalization.

### B. Deterministic Quality Engine (`mdrap.quality`)
- Implements 15 deterministic microsecond validation checks.
- Adheres to the strict priority invariant: `INVALID > SUSPICIOUS > VALID`. Status can never be downgraded once flagged.
- Evaluates bid/ask crossing, order book inversion, clock skew against local monotonic clocks, sequence continuity, and extreme price volatility.

### C. Provenance & Assurance Tracker (`mdrap.provenance`)
- Forms an unforgeable cryptographic audit trail for every event.
- Lineage hash binds the raw wire frame, venue identifier, sequence number, and quality outcome into a single SHA-256 digest:
$$\text{LineageHash} = \text{SHA256}(\text{EventID} \parallel \text{RawID} \parallel \text{Provider} \parallel \text{Instrument} \parallel \text{PayloadSHA256} \parallel \text{Status} \parallel \text{Timestamp})$$

### D. Incident Management (`mdrap.incidents`)
- Clusters anomalies into structured `DataQualityIncident` episodes.
- Captures time corridors (5 seconds before to 5 seconds after an anomaly) for forensic reconstruction.
- Records provider disconnections, sequence discontinuities, and book inversions.

### E. Merkle Audit Tree (`mdrap.security`)
- Computes cryptographic Merkle trees conforming to **RFC 6962**.
- Applies strict domain separation:
  - Leaf hash: $\text{SHA256}(0\text{x}00 \parallel \text{leaf\_data})$
  - Node hash: $\text{SHA256}(0\text{x}01 \parallel \text{left\_child} \parallel \text{right\_child})$
- Generates compact $O(\log N)$ inclusion proofs for external compliance audits.

### F. Deterministic Incident Replay (`mdrap.replay`)
- Plays back historical incident corridors under an isolated `VirtualClock`.
- Reconstructs exact order book and BBO dynamics tick-by-tick.
- Verifies that replaying an incident produces a bit-for-bit identical state digest to original execution.

---

## 3. Storage Architecture

- **Engine**: Embedded SQLite in Write-Ahead Logging (`WAL`) mode.
- **Transactions**: Batched writes using `executemany` with `PRAGMA synchronous = NORMAL`.
- **Zero External Broker**: No Kafka, Redis, or RabbitMQ required for core functionality, eliminating operational complexity and network-partition failure modes.
