# Architecture Explainer: Inside the MDRAP Reliability Sidecar

**Target Audience**: Lead Infrastructure Engineers, Core Developers, Quantitative Architects  
**Scope**: Ingestion, Normalization, Validation, Persistence, Replay, and Merkle Proofs  

---

## 1. Architectural Philosophy: The Passive Sidecar

In institutional low-latency architecture, placing a heavyweight broker or proxy directly in the critical execution path introduces jitter, head-of-line blocking, and point-of-failure vulnerabilities.

MDRAP avoids this by implementing the **Passive Sidecar Pattern**:
- It consumes data through read-only taps (public WebSocket feeds or raw socket mirrors).
- It normalizes, evaluates, and persists feeds out-of-band or via lockless shared-memory ring buffers.
- It operates with zero order-routing capabilities, eliminating execution risk.

```
       [Market Data Feed]
               |
               +------------------------------------+
               | (Direct to Low-Latency Engine)     | (Passive Tap)
               v                                    v
     [Execution Algorithm]                  [MDRAP Sidecar]
               |                            - 15-Rule Validation
               | (Out-of-Band Circuit Alert)| - SHA-256 Provenance
               <============================| - SQLite WAL Storage
                                            - RFC 6962 Merkle Trees
```

---

## 2. Ingestion & Canonical Normalization

Raw messages enter the `Gateway` via provider-specific parsers. Each message undergoes:
1. **Payload Hashing**: `compute_payload_hash(raw_wire)` computes a deterministic SHA-256 hash before parsing.
2. **Tri-Timestamp Vectoring**: Captures `(exchange_ts, receive_ts, processing_ts)`. The receive timestamp is stamped immediately upon socket read using high-resolution monotonic clocks (Windows QPC or Linux `CLOCK_MONOTONIC_RAW`).
3. **Monotonic Event ID Assignment**: Generates sequential IDs using `itertools.count()`, completely avoiding non-deterministic `uuid.uuid4()` system calls.
4. **Canonical Event Transformation**: Emits a `CanonicalEvent` representing L1 Best Bid/Offer, L2 Book Depths, or Trades.

---

## 3. The 15-Rule Deterministic Quality Engine

The `QualityEngine` evaluates incoming events against 15 strict invariants:
- **Structural Integrity**: Non-positive prices, non-positive sizes, corrupt CRC delimiters, SBE boundary violations.
- **Market Microstructure**: Inverted spreads (bid $\ge$ ask), excessive spreads ($>500\text{ bps}$), price jumps ($>10\%$ moving median divergence).
- **Network & Protocol Continuity**: Sequence gaps ($\Delta S \ge 2$), sequence regressions ($S_t < S_{t-1}$), duplicate sequences, clock skew ($>50\text{ms}$), stale ticks ($>3.0\text{s}$).

### Non-Downgrade Hierarchy
The engine enforces:
$$\text{INVALID} > \text{SUSPICIOUS} > \text{VALID}$$
Status is strictly monotonic: once an anomaly is detected, no downstream aggregator or reconciler can downgrade its severity.

---

## 4. Cryptographic Provenance & Merkle Anchoring

To establish an unforgeable audit trail, MDRAP constructs a **Lineage Hash** for every event:
$$\text{LineageHash} = \text{SHA256}(\text{event\_id} \parallel \text{raw\_id} \parallel \text{provider} \parallel \text{instrument} \parallel \text{data\_mode} \parallel \text{payload\_sha256} \parallel \text{quality\_status} \parallel \text{processing\_timestamp})$$

Events and incidents are grouped into batches and anchored into an **RFC 6962 Merkle Tree** with domain separation (`0x00` leaf, `0x01` interior node).

---

## 5. Deterministic Historical Replay

When an incident occurs, MDRAP records the entire time corridor into SQLite WAL. Researchers invoke `IncidentReplayEngine` to replay the corridor under `VirtualClock`:
- Recreates the synthetic order book tick-by-tick.
- Re-evaluates quality rules without system clock jitter.
- Computes a deterministic SHA-256 state digest confirming bit-for-bit reproducibility.
