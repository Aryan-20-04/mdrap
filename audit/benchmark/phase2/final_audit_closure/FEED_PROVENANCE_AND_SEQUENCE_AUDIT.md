# MDRAP Phase 2.3 — Feed Provenance & Sequence Tracking Audit

**Audit Target:** Data Origin, Upstream Venue Emulation, Sequence Domains, and Schema Omissions  
**Evaluation Git Branch:** `main`  
**Evaluation HEAD Commit:** `51f99e8e18768860540b534297b02b507cefce05`  
**Clean Release Reference:** `maintenance/architecture-pruning-v608-clean` @ `38ec051e0958c2199268a03afb54f6f24b89cd58`  
**Auditor:** Independent Quality & Technical Certification Directorate  
**Governance Policy:** STRICTLY READ-ONLY. NO PRODUCTION DEPLOYMENT. NO CAPITAL AT RISK.

---

## 1. Executive Summary

This forensic report investigates the upstream provenance of market data processed during the Phase 2.2 Two-Hour Reliability Soak benchmark and audits the multi-tier sequence domain architecture implemented across the platform.

The audit independently establishes two critical forensic determinations:
1. **The benchmark workload was 100% synthetically generated in-memory.** Zero external exchange connections, WebSocket connections, or historical PCAP/ITCH replay files were used.
2. **Per-venue upstream sequence tracking was completely bypassed in the Observer sidecar.** Due to an architectural omission in `MarketDataDaemon._process_and_broadcast()`, the upstream venue sequence number was never serialized into the TCP broadcast payload dictionary. Consequently, the Observer received default `feed_seq = 0` for all 6,984,800 events, which caused `VenueHealth.update_sequence()` to silently skip continuity checks.

While the daemon's internal transport sequence was rigorously verified without loss ($6,984,800 / 6,984,800$, $0$ gaps), historical claims that the benchmark validated **"five live exchange feeds with zero venue sequence gaps" are factually false, disproven by source code, and formally refuted.**

---

## 2. In-Memory Synthetic Data Generation Audit

### 2.1. Generation Code Inspection
In `benchmarks/run_phase2_2h_soak.py` (lines 312–341), every event injected into the platform originated from the following loop:

```python
# benchmarks/run_phase2_2h_soak.py (lines 312-341)
for _ in range(batch_size):
    producer_sent += 1
    window_sent += 1
    producer_id_xor ^= producer_sent
    producer_id_sum += producer_sent
    producer_max_id = producer_sent

    venue = venues[producer_sent % len(venues)]
    symbol = symbols[producer_sent % len(symbols)]
    base_p = 65000.0 if "BTC" in symbol else 3400.0
    price = base_p + (producer_sent % 100) * 0.1
    t_pub = time.perf_counter()

    raw = RawEvent(
        source=venue,
        receive_timestamp=t_pub,
        payload={
            "instrument": symbol,
            "event_type": "TICK",
            "price": price,
            "bid": price - 0.5,
            "ask": price + 0.5,
            "bid_size": 10.0,
            "ask_size": 10.0,
            "quantity": 1.0,
            "sequence": producer_sent,
            "exchange_ts": time.time(),
        },
    )
    daemon._process_and_broadcast(raw)
```

### 2.2. Provenance Findings
1. **No External Network Sockets:** The runner never initialized outbound HTTP, WebSocket, or FIX connections to external exchange matching engines.
2. **No Recorded Market Traces:** No historical PCAP captures, NASDAQ ITCH files, or replayed L2 order book traces were mounted or read.
3. **Deterministic Formulaic Streams:**
   - Venues: Round-robin selection across 5 string constants (`BINANCE`, `COINBASE`, `KRAKEN`, `OKX`, `BYBIT`).
   - Symbols: Round-robin selection across 4 string constants (`BTCUSDT`, `BTC-USD`, `ETHUSDT`, `ETH-USD`).
   - Prices: Derived from static mathematical formulas: `base_p + (producer_sent % 100) * 0.1`.
   - Spreads: Fixed constants: `bid = price - 0.5`, `ask = price + 0.5`.
4. **Classification:** The dataset was 100% synthetic, designed strictly for local throughput, memory stability, and internal transport stress testing.

---

## 3. The Four Distinct Sequence & Continuity Domains

Institutional market data systems manage multiple distinct layers of sequence and continuity tracking:

```
+----------------------------------------------------------------------------------------------------+
|                                    SEQUENCE DOMAIN HIERARCHY                                       |
+----------------------------------------------------------------------------------------------------+
| Domain 1: Exchange Feed Continuity  | Upstream matching engine sequence over public networks      |
| Domain 2: Venue Feed Continuity     | Per-adapter normalized packet sequence (Binance, Coinbase)   |
| Domain 3: Transport Stream Seq      | Monotonic sequence emitted by local MDRAP broadcast daemon  |
| Domain 4: Event Chronological Order | Microsecond ordering across mixed-venue time series         |
+----------------------------------------------------------------------------------------------------+
```

1. **Domain 1 (Exchange Feed Continuity):** Detects dropped packets over public internet/WAN connections (e.g., Binance WebSocket sequence drops). **NOT TESTED** (No live internet connection).
2. **Domain 2 (Venue Feed Continuity):** Tracks per-venue packet sequencing within the normalization sidecar. **BYPASSED** (Due to payload schema omission).
3. **Domain 3 (Transport Stream Sequence):** Monotonically increasing broadcast sequence assigned by `MarketDataDaemon._next_seq()` to downstream TCP clients. **VERIFIED** (6,984,800 events delivered without gaps).
4. **Domain 4 (Event Chronological Order):** Ingest and broadcast timestamps assigned by the platform. **VERIFIED** (Monotonically tracked without reordering).

---

## 4. Root Cause of the Venue Sequence Tracking Bypass

### 4.1. Defect in Broadcast Payload Serialization
In `src/mdrap/service.py` (lines 867–898), `MarketDataDaemon._process_and_broadcast()` constructs the payload dictionary sent to TCP subscribers:

```python
# src/mdrap/service.py (lines 867-898)
bbo_q = self.bbo.current_bbo(ev.instrument_id)
t_broadcast = time.time()
seq = self._next_seq()
self._total_broadcast += 1

payload = {
    "type": "TICK",
    "seq": seq,                      # <--- Local daemon transport sequence
    "sym": ev.instrument_id,
    "event": ev.event_type.value,
    "price": ev.price,
    "size": ev.quantity,
    "bid": ev.bid_price,
    "ask": ev.ask_price,
    "bid_size": ev.bid_size,
    "ask_size": ev.ask_size,
    "source": ev.source,
    "status": ev.quality_status.value,
    "exchange_ts": ev.exchange_timestamp,
    "ingest_ts": raw.receive_timestamp,
    "broadcast_ts": t_broadcast,
    "proc_us": round(lat_us, 1),
    "engine_us": round(lat_us, 1),
    "bbo": ...
}
```

**Architectural Defect:** While the incoming `RawEvent` payload had `"sequence": producer_sent` (which was mapped to `ev.sequence_number` in the internal `MarketEvent` dataclass), **`ev.sequence_number` was never included in the broadcast `payload` dictionary!** The only sequence field transmitted over the TCP socket was `"seq": seq` (the daemon transport sequence).

### 4.2. Observer Parsing Logic
In `external_sidecar/mdrap_observer/observer/state.py` (lines 115–125), `ObserverState.process_event()` parses incoming dictionaries:

```python
# external_sidecar/mdrap_observer/observer/state.py
stream_seq = _safe_int(event.get("seq"))
feed_seq = _safe_int(
    event.get("feed_seq")
    or event.get("venue_seq")
    or event.get("channel_seq")
    or event.get("sequence_number")
)
if feed_seq == 0 and stream_seq == 0 and "sequence" in event:
    feed_seq = _safe_int(event.get("sequence"))
```

### 4.3. The Silent Bypass in `VenueHealth.update_sequence`
1. The TCP message contained `"seq": seq` (e.g., $1, 2, \dots, 6,984,800$), so `stream_seq` was positive.
2. The fields `feed_seq`, `venue_seq`, `channel_seq`, and `sequence_number` were missing from the message.
3. The fallback condition evaluated to `False` because `stream_seq != 0`.
4. Therefore, **`feed_seq` remained exactly 0 for every event.**
5. In `VenueHealth.update_sequence()` (lines 190–205):
   ```python
   def update_sequence(self, seq: int, feed_type: str = "LIVE") -> None:
       self.events_received += 1
       self.last_seen_ts = time.time()
       self.status = "CONNECTED"
       self.feed_type = feed_type
       if seq > 0:  # <--- Evaluates to False when seq == 0!
           if self.last_feed_seq >= 0:
               if seq > self.last_feed_seq + 1:
                   self.feed_gaps += (seq - self.last_feed_seq - 1)
               elif seq == self.last_feed_seq:
                   self.feed_duplicates += 1
               elif seq < self.last_feed_seq:
                   self.feed_out_of_order += 1
           self.last_feed_seq = seq
   ```
6. Because `seq == 0`, the entire sequence validation block was bypassed.
   - `self.feed_gaps` remained 0.
   - `self.feed_duplicates` remained 0.
   - `self.feed_out_of_order` remained 0.

---

## 5. Formal Invalidation of Multi-Venue Live Claims

Based on the forensic evidence, historical claims regarding live multi-venue validation must be retracted:

1. **"Zero Feed Gaps Across All 5 Venue Adapters":** **FAILED / ARTIFACT**. The metric `feed_gaps = 0` was an artifact of sequence checking being bypassed due to payload schema omission. It did not validate venue feed continuity.
2. **"Live Multi-Venue Feed Validation (Binance, Coinbase, Kraken, OKX, Bybit)":** **FAILED / UNTRUE**. All five venues were string labels randomly cycling in an in-memory loop. No exchange protocol was executed.
3. **MDRAP Transport Sequence Integrity:** **VERIFIED**. `state.stream_health.update(stream_seq)` was actively executed on every event, proving that the **daemon transport sequence** had zero gaps, zero duplicates, and zero out-of-order frames.

---

## 6. Audit Verdicts Summary

1. **Synthetic Workload Nature:** **VERIFIED** (100% in-memory generated).
2. **Absence of Live Exchange Connectivity:** **VERIFIED**.
3. **MDRAP Transport Stream Continuity (6.98M events, 0 gaps):** **VERIFIED**.
4. **Venue Feed Sequence Tracking Defect:** **VERIFIED** (Schema omission caused bypass).
5. **"Live Feed Continuity" Claim:** **FAILED / REFUTED**.
6. **"Zero Venue Feed Gaps" Claim:** **FAILED / ARTIFACT**.
