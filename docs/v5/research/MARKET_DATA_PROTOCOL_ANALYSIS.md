# MDRAP v5.0 — Market Data Protocol & Feed Sequencing Analysis

**Document ID:** `docs/v5/research/MARKET_DATA_PROTOCOL_ANALYSIS.md`  
**Classification:** Research Analysis & Protocol Specification  
**Auditor / Architect:** Principal Low-Latency Systems Architect & Market Data Protocol Specialist  
**Evidence Standard:** Primary Exchange Specifications & Technical Standards  

---

## 1. Primary References & Protocol Specifications

1. **Nasdaq TotalView-ITCH 5.0 Specification**:
   - URL: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification_5.0.pdf
   - Framing Protocol: MoldUDP64 Protocol Specification (Nasdaq Trader)
2. **FIX Simple Binary Encoding (SBE) Specification**:
   - URL: https://fixtrading.org/standards/technical-standards/
   - Specification: FIX High Performance Working Group SBE v1.0 / v2.0
3. **CME MDP 3.0 (Market Data Platform)**:
   - SBE message template framing over UDP multicast channels with dual A/B feed arbitration.

---

## 2. Core Protocol Framing & Decoding Mechanisms

### 2.1 Message Framing & MoldUDP64 Packet Layout
- `[EXTERNAL-RESEARCH]` Nasdaq ITCH streams are delivered over **MoldUDP64** transport:
  - **MoldUDP64 Header (20 bytes)**:
    - `Session (10 bytes)`: Alphanumeric session identifier.
    - `Sequence Number (8 bytes)`: 64-bit unsigned integer representing the sequence of the first message in the packet.
    - `Message Count (2 bytes)`: Number of bundled ITCH messages in the packet ($0\text{--}65,535$). A count of `0xFFFF` indicates an End-of-Session heartbeat.
  - **Message Block**:
    - `Length (2 bytes)`: Length of the subsequent ITCH message.
    - `Message Data ($N$ bytes)`: ITCH message beginning with a 1-byte `MessageType` (e.g. `'S'` System Event, `'R'` Directory, `'A'` Add Order, `'E'` Order Executed, `'P'` Trade).

### 2.2 FIX Simple Binary Encoding (SBE)
- `[EXTERNAL-RESEARCH]` SBE is designed for zero-copy CPU-native deserialization:
  - Fixed-size message headers: `blockLength (uint16)`, `templateId (uint16)`, `schemaId (uint16)`, `version (uint16)`.
  - Fields are positioned to align with natural machine boundaries (2, 4, 8 bytes) to avoid unaligned memory access penalties.
  - Fixed-length fields precede repeating groups and variable-length data.
- **MDRAP Evaluation `[SOURCE-VERIFIED]` `[BENCHMARK-MEASURED]`**:
  - In `benchmarks/stage_breakdown.json`:
    - Python JSON Decode: $195,694\text{ EPS}$ ($3.7\ \mu\text{s}$ p50).
    - Binary SBE Decode: $1,136,798\text{ EPS}$ ($0.6\ \mu\text{s}$ p50).
    - **Speedup: 5.81x faster decoding**.
  - MDRAP v5.0 must natively support SBE framing on the hot path while maintaining JSON adapters for testing and telemetry.

---

## 3. Feed/Channel Sequencing vs. Instrument-Level Partitioning

A critical structural trap in multi-threaded market data architecture is **premature partitioning**:

```
WRONG ARCHITECTURE (Conceals Sequence Gaps):
Exchange Packet ──► [Hash by Instrument] ──► Thread A (AAPL) ──► Seq Check (Sees gaps because MSFT was stripped!)
                                         ──► Thread B (MSFT) ──► Seq Check
```

### The Architectural Invariant
$$\mathbf{Feed\ Ingress \longrightarrow Channel\ Sequencing\ \&\ Gap\ Detection \longrightarrow Deterministic\ Dispatch \longrightarrow Instrument\ State}$$

1. **Feed/Channel-Level Ordering**:
   - `[EXTERNAL-RESEARCH]` Exchange sequence numbers in MoldUDP64 / ITCH / MDP 3.0 belong to the **communication channel**, NOT the individual stock symbol.
   - Channel 1 carries messages for AAPL, MSFT, GOOGL, NVDA interleaved sequentially ($seq = 100, 101, 102, 103$).
   - If packet 102 (MSFT) is dropped by the network, Channel 1's sequence counter observes $101 \rightarrow 103$. This represents an **authoritative channel sequence gap**.
2. **Consequence of Premature Instrument Routing**:
   - If packets are routed to worker threads by `hash(instrument)` before sequence validation:
     - Thread AAPL sees $seq = 100$, then $seq = 103$. It cannot determine whether $101$ and $102$ were MSFT messages or lost AAPL messages without cross-thread coordination!
3. **Mandated v5 Processing Order**:
   - **Stage 1: Channel Ingress & Sequence Validation (Single-Threaded per Channel)**:
     - Check MoldUDP64 sequence against channel expected watermark.
     - Detect gap $\rightarrow$ flag `SEQUENCE_GAP` and initiate MoldUDP64 retransmission request.
     - Detect duplicate $\rightarrow$ flag `DUPLICATE` or drop retransmission.
     - Detect session reset $\rightarrow$ advance epoch.
   - **Stage 2: Deterministic Dispatch**:
     - Route validated channel messages to instrument processing queues.
   - **Stage 3: Instrument-Owned State Processing (Parallel across Partitions)**:
     - Update BBO, order books, and running statistical price corridors.

---

## 4. Retransmission, Recovery & Session Boundaries

### 4.1 Retransmission Channels (MoldUDP64 Request Protocol)
- `[EXTERNAL-RESEARCH]` When a gap is detected, the receiver sends a unicast UDP `Request Packet` to the exchange retransmission server specifying `Session`, `Sequence Number`, and `Count`.
- The retransmitted packets arrive on a separate unicast socket out-of-band while the live multicast feed continues.
- **Buffer / Re-order Strategy**:
  - Live packets arriving ahead of the gap must be held in a bounded circular reorder buffer (`reorder_window_s`).
  - When retransmitted packets arrive, they backfill the missing slots.
  - If the retransmission timeout expires, the gap is declared unrecoverable, the channel watermark is forced forward, and downstream consumers receive an explicit `REASON_SEQUENCE_GAP` marker.

### 4.2 Session Boundaries & Daily Resets
- At end-of-day or system restart, the exchange issues a Session Reset message or begins a new alphanumeric session ID.
- In MDRAP:
  - A session change unconditionally clears feed-level sequence numbers (`seen` bitmap and `stale_seen` ring).
  - Prevents false duplicate classifications when sequence numbers restart at $1$ on the new session.

---

## 5. Architectural Verdict for MDRAP v5.0

1. **Channel-First Architecture `[SOURCE-VERIFIED]`**:
   - All feed adapters must execute channel framing, sequence tracking, and gap detection **prior** to any thread dispatch or instrument-level partitioning.
2. **SBE Native Decoding `[SOURCE-VERIFIED]`**:
   - Provide direct C struct overlays for ITCH 5.0 and SBE message types, eliminating Python dictionary conversion on the hot path.

---

## 6. Evidence Classification Summary

- `[EXTERNAL-RESEARCH]` Nasdaq TotalView-ITCH 5.0 and MoldUDP64 official specifications.
- `[SOURCE-VERIFIED]` `src/mdrap/itch.py`, `src/mdrap/sbe.py`, `src/mdrap/engine.py`.
- `[BENCHMARK-MEASURED]` `audit/v5/raw_evidence/benchmark_stage_breakdown.log` (5.81x SBE decode speedup).
