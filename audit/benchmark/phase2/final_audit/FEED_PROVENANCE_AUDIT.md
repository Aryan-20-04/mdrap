# MDRAP 6.0.8 — Phase 2.3: Feed Provenance & Sequence Domain Forensic Audit

**Audit Date:** 2026-11-21  
**Audit Target:** Upstream Data Provenance & Sequence Domain Mechanics  
**Evaluated Commit:** `51f99e8e18768860540b534297b02b507cefce05` (`main`)  
**Clean Release Reference:** `38ec051e0958c2199268a03afb54f6f24b89cd58` (`maintenance/architecture-pruning-v608-clean`)  
**Auditor:** Independent Quality & Certification Auditor  
**Governance:** STRICTLY READ-ONLY. NO DEPLOYMENT. NO LIVE TRADING. NO STRATEGY ACTIVATION.

---

## 1. Executive Summary

This forensic report investigates the provenance of market data processed during the Phase 2.2 Two-Hour Reliability Soak benchmark and audits the exact sequence domain tracking mechanisms implemented in both the producer and the Observer sidecar.

A critical finding of this audit is that **the benchmark workload was 100% synthetically generated in-memory**, and that **per-venue upstream sequence tracking was completely bypassed in the Observer** due to a schema omission in the TCP broadcast payload. While the daemon's transport sequence was rigorously verified without a single gap across 6,984,800 events, claims that the benchmark validated "five live exchange feeds with zero venue sequence gaps" are factually incorrect and rejected.

### Provenance & Sequence Certification Matrix

| Area | Evaluated Assertion | Forensic Reality | Certification Verdict |
| :--- | :--- | :--- | :--- |
| **Data Provenance** | "Multi-venue market data feed validation" | 100% synthetically generated via deterministic round-robin formulas | **FAILED / UNTRUE** |
| **Exchange Connectivity** | "Live exchange feeds (BINANCE, COINBASE, etc.)" | Zero external network sockets; simulated string tags only | **FAILED / UNTRUE** |
| **PCAP / Historical Replay** | "Recorded market data playback" | No PCAP, ITCH, or SBE packet capture files used | **FAILED / UNTRUE** |
| **Daemon Transport Sequence**| Monotonic stream delivery without gaps ($1 \dots N$) | Validated: `seq_gaps = 0`, `seq_duplicates = 0`, `seq_ooo = 0` | **VERIFIED** |
| **Per-Venue Sequence Tracking**| "Per-venue feed sequence integrity validated" | Bypassed: TCP payload omitted `feed_seq`, causing `feed_seq = 0` | **FAILED / UNVERIFIABLE** |
| **Reported `feed_gaps = 0`** | "Zero feed sequence gaps across all 5 venues" | Artifact of `feed_seq == 0` bypassing `VenueHealth.update_sequence` | **FAILED / ARTIFACT** |

---

## 2. Forensic Code Trace of Event Generation

Inspection of `benchmarks/run_phase2_2h_soak.py` (lines 312–341) documents the exact origin of every event processed during the 2-hour soak:

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

### Forensic Findings on Data Provenance:
1. **No External Network Feeds:** The runner created zero outbound sockets to external venues (Binance, Coinbase, Kraken, OKX, Bybit).
2. **No Replayed Market Traces:** No historical L2/L3 order book captures, PCAP files, or exchange trade logs were mounted or read.
3. **Deterministic Synthetic Formulas:**
   - Venues were assigned by modulo index: `venues[producer_sent % len(venues)]`.
   - Symbols were assigned by modulo index: `symbols[producer_sent % len(symbols)]`.
   - Prices were generated via mathematical arithmetic: `base_p + (producer_sent % 100) * 0.1`.
   - BBO spreads were static constants: `bid = price - 0.5`, `ask = price + 0.5`.
4. **Conclusion:** The dataset was entirely synthetic, designed to benchmark throughput, memory, and transport reliability under idealized deterministic conditions.

---

## 3. Dual Sequence Domains: Transport Stream vs. Venue Feed

Institutional market data systems manage two separate sequence domains:
1. **Transport Stream Sequence ($S_{\text{transport}}$):** Monotonically increasing sequence assigned by the local normalization engine/daemon to its downstream broadcast stream.
2. **Venue Feed Sequence ($S_{\text{venue}}$):** Monotonically increasing sequence assigned by an upstream exchange matching engine (e.g., Binance WebSocket sequence or NASDAQ ITCH sequence) to detect packet loss over the public internet.

### 3.1. What Was Broadcast by `MarketDataDaemon`
Inspection of `src/mdrap/service.py` (lines 867–898) reveals the payload dictionary serialized for downstream TCP clients:

```python
# src/mdrap/service.py (lines 867-888)
seq = self._next_seq()
self._total_broadcast += 1

payload = {
    "type": "TICK",
    "seq": seq,                      # Local transport sequence
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

**Critical Observation:** The raw event's upstream sequence (`payload["sequence"]`) was mapped to `ev.sequence_number` in the internal `MarketEvent` dataclass, but **it was never serialized into the downstream broadcast `payload` dictionary!** The only sequence field present in the broadcast dictionary was `"seq": seq`, representing the daemon's internal transport sequence.

---

## 4. Root Cause of Bypassed Venue Sequence Tracking in Observer

In `external_sidecar/mdrap_observer/observer/state.py` (lines 115–130), `ObserverState.process_event()` parses incoming TCP messages:

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

### 4.1. Execution Trace for Soak Events
1. The incoming `event` dictionary contained `"seq": seq` (where `seq` was $1, 2, \dots, 6,984,800$).
2. `stream_seq` was correctly extracted as `seq` (e.g., $1, 2, \dots$).
3. The fields `feed_seq`, `venue_seq`, `channel_seq`, and `sequence_number` did not exist in the dictionary.
4. The fallback condition `if feed_seq == 0 and stream_seq == 0 and "sequence" in event:` evaluated to `False` because `stream_seq != 0`.
5. Therefore, **`feed_seq` remained exactly 0** for all 6,984,800 events!

### 4.2. How `VenueHealth.update_sequence()` Handled `feed_seq = 0`
In `external_sidecar/mdrap_observer/observer/state.py` (lines 190–205):

```python
def update_sequence(self, seq: int, feed_type: str = "LIVE") -> None:
    self.events_received += 1
    self.last_seen_ts = time.time()
    self.status = "CONNECTED"
    self.feed_type = feed_type
    if seq > 0:  # <--- Bypassed completely when seq == 0!
        if self.last_feed_seq >= 0:
            if seq > self.last_feed_seq + 1:
                self.feed_gaps += (seq - self.last_feed_seq - 1)
            elif seq == self.last_feed_seq:
                self.feed_duplicates += 1
            elif seq < self.last_feed_seq:
                self.feed_out_of_order += 1
        self.last_feed_seq = seq
```

Because `seq` was passed as `feed_seq = 0`, the entire sequence check block (`if seq > 0:`) was skipped for every event.
- `self.feed_gaps` remained 0.
- `self.feed_duplicates` remained 0.
- `self.feed_out_of_order` remained 0.
- `self.last_feed_seq` remained -1.

### 4.3. Forensic Assessment
- The reported metric `feed_gaps = 0` across all five venues did **not** prove venue sequence continuity.
- It was a silent artifact of sequence tracking being bypassed due to payload schema omission.
- In contrast, `state.stream_health.update(stream_seq)` was actively executed on every event, proving that the **daemon transport sequence** had zero gaps, zero duplicates, and zero out-of-order deliveries.

---

## 5. Feed Provenance Audit Verdicts

1. **Synthetic Workload Nature:** **VERIFIED**. The benchmark tested purely synthetic, in-memory generated events.
2. **Live Feed Claim:** **FAILED / UNTRUE**. No live exchange connectivity was used or validated.
3. **Transport Stream Sequence Integrity:** **VERIFIED**. The daemon's internal transport sequence was delivered without loss ($6,984,800 / 6,984,800$, $0$ gaps).
4. **Venue Feed Sequence Integrity:** **FAILED / UNVERIFIABLE**. Venue feed sequence verification was bypassed due to missing dictionary fields in the broadcast serialization.
