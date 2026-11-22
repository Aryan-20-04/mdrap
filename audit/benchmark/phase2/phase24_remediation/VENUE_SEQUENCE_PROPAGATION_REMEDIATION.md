# MDRAP Phase 2.4 — Upstream Venue Sequence Propagation Remediation Report

## 1. Executive Summary & Root Cause Analysis

In the Phase 2.3 Independent Forensic Audit of the MDRAP platform, a critical architectural gap was identified regarding sequence integrity monitoring:
- **Root Cause**: The broadcast stream emitted by `MarketDataDaemon` over TCP previously transmitted an internal transport broadcast counter (`seq`), but omitted the upstream venue-native sequence number (e.g. Binance `lastUpdateId`/`u`, Kraken message sequence, Coinbase sequence, ITCH SBE sequence).
- **Misleading Audit Claims**: Because the downstream `ObserverState` tracked sequence numbers exclusively from the incoming JSON payload, it tracked the daemon's internal monotonic counter `seq`. As a result, the Observer reported **0 sequence gaps**, which reflected only that the local TCP loopback connection did not drop bytes—it failed completely to monitor whether upstream exchanges or feed adapters experienced message drops.
- **Silent Sequence Coercion**: Furthermore, earlier prototypes defaulted missing sequence numbers to `0`, resulting in sequence ambiguity between feeds that legitimately begin at sequence `0` and feeds that lack sequence numbers entirely.

In Phase 2.4, this architecture has been completely remediated. The system now enforces a strict dual-sequence domain model across serialization, transport, and observer monitoring.

---

## 2. Dual Sequence Domain Architecture

MDRAP explicitly decouples sequence tracking into two strictly independent domains:

```
┌────────────────────────────────────────────────────────┐
│               UPSTREAM EXCHANGE / FEED                 │
│  Venue Sequence Domain: feed_seq / venue_seq           │
│  - Assigned by exchange matching engine / gateway      │
│  - Monitored per venue & per symbol                    │
│  - Detects exchange drops, gateway packet loss, resets │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│                 MDRAP CORE DAEMON                      │
│  - Normalizes & evaluates quality                      │
│  - Assigns monotonic broadcast counter: seq            │
│  - Serializes BOTH seq AND feed_seq to TCP payload     │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│             DOWNSTREAM OBSERVER SIDECAR                │
│  Stream Health:        Venue Health:                   │
│  - Tracks 'seq'        - Tracks 'feed_seq'             │
│  - Detects TCP drops   - Tracks exchange integrity     │
│  - Detects disconnects - Exposes 'feed_seq_unavailable'│
└────────────────────────────────────────────────────────┘
```

### Domain Characteristics:
1. **Transport Stream Sequence (`seq`)**:
   - **Scope**: Single daemon process broadcast stream.
   - **Generator**: `MarketDataDaemon._next_seq()` (monotonic integer counter).
   - **Purpose**: Detects TCP socket buffer drops, client queue evictions, network interface packet loss, and abrupt client reconnections.
2. **Venue Feed Sequence (`feed_seq` / `venue_seq`)**:
   - **Scope**: Specific upstream venue and symbol (e.g., `BINANCE:BTC-USDT`).
   - **Generator**: External exchange or adapter gateway.
   - **Purpose**: Detects upstream packet drops, crossed books, gap recovery failures, and exchange session resets.

---

## 3. TCP Serialization & Daemon Remediation

### 3.1 Robust Type Extraction in `_process_and_broadcast()`
In `src/mdrap/service.py`, `_safe_int_val()` was introduced to guard against non-integer or malformed sequence values (e.g. `'N/A'`, empty strings, or floating point strings) that may appear in simulated or corrupted feeds:

```python
def _safe_int_val(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
```

During canonical event normalization and broadcast payload construction, the daemon resolves the venue sequence from:
1. `ev.sequence_number` (populated from canonical normalization), OR
2. `payload_dict` keys in fallback paths: `"sequence"`, `"feed_seq"`, `"venue_seq"`, or `"channel_seq"`.

### 3.2 Wire Payload Schema
The daemon's TCP JSON broadcast payload now explicitly transmits both sequence identifiers:

```json
{
  "type": "TICK",
  "seq": 10452,
  "feed_seq": 8943210,
  "venue_seq": 8943210,
  "sym": "BTC-USDT",
  "event": "TICK",
  "price": 65420.5,
  "size": 0.25,
  "source": "BINANCE",
  "status": "VALID",
  "t0_ns": 1729500000000000,
  "broadcast_ts_ns": 1729500000012500,
  "engine_ns": 12500,
  "ingest_ts_ns": 1729499999980000
}
```

**Non-Coercion Guarantee:**
If the upstream event does not supply a venue sequence number, `"feed_seq"` and `"venue_seq"` are serialized as `null` (`None`). Under no circumstances is an absent sequence defaulted to `0`.

---

## 4. Observer Sidecar Parsing & Diagnostics

### 4.1 Strict Optional Integer Parsing (`_parse_optional_int`)
In `external_sidecar/mdrap_observer/observer/state.py`, parsing logic was updated to strictly differentiate between `None` (absent) and `0` (a legitimate sequence number):

```python
def _parse_optional_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
```

### 4.2 Diagnostic Metric: `feed_seq_unavailable`
A new diagnostic counter `feed_seq_unavailable` was added to `VenueHealth`:
- When an event arrives with `feed_seq is None`:
  - `self.feed_seq_unavailable += 1` is incremented.
  - `self.events_received += 1` and `self.last_seen_ts` are updated.
  - **Crucial Rule**: Gap, duplicate, and out-of-order counters are **not touched**. The Observer never claims "0 sequence gaps" on feeds where sequences are unavailable; instead, it explicitly reports that sequences were unavailable.

### 4.3 Legitimate Sequence 0 Support
Protocols that start their sequence counters from `0` (such as certain SBE/binary protocols or synthetic replay feeds) are fully supported:
- If `last_feed_seq is None` and `seq == 0`: `has_sequence_feed` is set to `True`, and `last_feed_seq` becomes `0`.
- Subsequent sequence `1` is recognized as continuous ($\Delta = 1$, zero gaps).

### 4.4 Transition & Integrity State Machine
When `seq is not None`, `VenueHealth.update_sequence` executes a formal integrity state machine:
- **Continuous Step**: If `seq == self.last_feed_seq + 1`: continuous; `self.last_feed_seq = seq`.
- **Forward Gap**: If `seq > self.last_feed_seq + 1`:
  - $\Delta = seq - (self.last\_feed\_seq + 1)$ is added to `self.gaps`.
  - An integrity event is logged.
  - `self.last_feed_seq = seq`.
- **Duplicate Sequence**: If `seq == self.last_feed_seq`:
  - `self.duplicates += 1` is incremented.
- **Out-of-Order Delivery**: If `seq < self.last_feed_seq`:
  - If `seq <= 1 and self.last_feed_seq > 10`: Recognized as a **reconnection or epoch reset**. `self.feed_resets += 1` is incremented, and `self.last_feed_seq = seq` without incrementing false gaps or out-of-order errors.
  - Otherwise: `self.out_of_order += 1` is incremented.
- **Snapshot Resets**: When `is_snapshot=True` is flagged, `self.feed_snapshots += 1` is incremented, and `self.last_feed_seq = seq` is initialized without gap penalties.

---

## 5. Domain Disambiguation Proof

To prove complete decoupling between transport and venue sequence domains, two adversarial test cases were designed and verified:

### Scenario A: Transport Gap with Continuous Venue Sequence
- **Condition**: TCP connection drops 3 events (transport `seq` jumps from 100 to 104). The 3 events that arrive carry consecutive venue sequences: 501, 502, 503.
- **Result**:
  - `stream_health.gaps` = 3 (transport drop correctly detected).
  - `venue_health.gaps` = 0 (venue feed verified continuous).

### Scenario B: Venue Gap with Continuous Transport Sequence
- **Condition**: Daemon receives and broadcasts continuous stream ticks (transport `seq`: 1, 2, 3). However, the upstream exchange feed skipped sequences: 200, 205, 206.
- **Result**:
  - `stream_health.gaps` = 0 (transport stream verified 100% delivered).
  - `venue_health.gaps` = 4 (upstream venue drop of 4 messages correctly detected).

---

## 6. Verification Status

| Requirement | Implementation Anchor | Test Anchor | Verdict |
| :--- | :--- | :--- | :--- |
| Independent Sequence Domains | `observer/state.py:StreamHealth` & `VenueHealth` | `test_transport_gap_with_continuous_venue_sequence` | **VERIFIED** |
| Venue Sequence TCP Serialization | `src/mdrap/service.py:_process_and_broadcast` | `test_venue_sequence_propagation_through_daemon_tcp_payload` | **VERIFIED** |
| Missing Sequence Diagnostics (`feed_seq_unavailable`) | `observer/state.py:VenueHealth.update_sequence` | `test_missing_venue_sequence_is_not_silently_zeroed` | **VERIFIED** |
| Sequence 0 Support | `observer/state.py:_parse_optional_int` | `test_sequence_number_zero_supported` | **VERIFIED** |
| Upstream Gap / Dup / Out-of-Order Tracking | `observer/state.py:VenueHealth.update_sequence` | `test_venue_sequence_gap_duplicate_and_out_of_order` | **VERIFIED** |
| Reconnect & Epoch Reset Handling | `observer/state.py:VenueHealth.update_sequence` | `test_reconnect_and_sequence_reset_behavior` | **VERIFIED** |
| Snapshot Alignment Semantics | `observer/state.py:VenueHealth.update_sequence` | `test_snapshot_sequence_semantics` | **VERIFIED** |
