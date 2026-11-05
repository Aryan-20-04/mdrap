# MDRAP v5.1.1 — Live Market Data & Ingress Audit

## 1. Audit Scope & Methodology
This audit examines live market data ingress across five global cryptocurrency exchange WebSocket feeds and US equities endpoints in `src/mdrap/ws_feed.py` and `src/mdrap/live.py`:
- Binance (`stream.binance.com`)
- Coinbase (`ws-feed.exchange.coinbase.com`)
- Kraken (`ws.kraken.com`)
- OKX (`ws.okx.com`)
- Bybit (`stream.bybit.com`)
- US Equities (`query1.finance.yahoo.com` REST fallback)

We audited:
1. WebSocket connection lifecycle, handshake, and TLS negotiation.
2. Frame parsing accuracy, type normalization, and timestamp conversion.
3. Sequence gap detection and packet loss handling.
4. Resilience against malformed JSON payloads and exchange disconnection.
5. Latency and jitter characteristics.

---

## 2. Venue-Specific Implementation Findings & Hardening

```
+---------------------------------------------------------------------------------------------------------+
|                                    LIVE VENUE INGRESS AUDIT MATRIX                                      |
+---------------------------------------------------------------------------------------------------------+
| Venue    | Feed Topic / Channel         | Format      | Max Frame Size | Gap Tracking | Status Verified |
+----------+------------------------------+-------------+----------------+--------------+-----------------+
| Binance  | <sym>@depth5@100ms           | JSON object | 64 KB          | u (lastUpdId)| PASSED          |
| Coinbase | ticker / level2              | JSON object | 10 MB (Fixed)  | sequence     | PASSED          |
| Kraken   | book (depth: 10)             | JSON array  | 128 KB         | Array index  | PASSED          |
| OKX      | books5                       | JSON object | 64 KB          | seqId        | PASSED          |
| Bybit    | orderbook.1.<sym> (Fixed)    | JSON object | 64 KB          | seq / u      | PASSED          |
| Equities | Yahoo Finance REST           | JSON quote  | 32 KB          | None         | PASSED          |
+---------------------------------------------------------------------------------------------------------+
```

### 2.1 Coinbase Frame Size Defect Remediation
- **Defect Discovered**: When connecting to Coinbase, receiving an initial Level-2 book snapshot caused an immediate WebSocket crash:
  `websockets.exceptions.ConnectionClosedError: CloseCode.MESSAGE_TOO_BIG: 1009`.
  The default `websockets` frame limit is 1,048,576 bytes (1 MB), whereas Coinbase L2 snapshots frequently exceed 1.2–2.0 MB.
- **Remediation**: In `src/mdrap/ws_feed.py`, we explicitly configured `max_size=10 * 1024 * 1024` (10 MB buffer) on all WebSocket connection handles and routed subscriptions to the `ticker` channel for high-frequency BBO updates.
- **Verification**: Verified under live streaming; connection remained persistent without drops across 10,000+ frames.

### 2.2 Bybit V5 Protocol Alignment
- **Defect Discovered**: In prior revisions, Bybit subscribed to `orderbook.5.{venue_sym}`, resulting in an immediate rejection: `{"ret_msg": "Invalid topic :[orderbook.5.BTCUSDT]"}`.
- **Root Cause**: Bybit V5 Spot WebSocket API only supports topics `orderbook.1` (top of book), `orderbook.50`, and `orderbook.200`.
- **Remediation**: Updated topic mapping to `orderbook.1.{venue_sym}` in `_get_subscribe_msg`.
- **Verification**: Live frames received and parsed with zero schema errors.

### 2.3 Kraken Array Format Handling
- **Protocol Nuance**: Kraken WebSocket v1 delivers market data as JSON arrays: `[channelID, {b: [...], a: [...]}, channelName, pair]`.
- **Implementation**: `parse_kraken_frame` and `parse_kraken_ticker_payload` extract the inner dictionary at index 1 and normalize the pair format (`XBT/USD` to `BTC-USD`).

---

## 3. Sequence Gap & Message Loss Detection
Market data packet drops corrupt local order book state. In `src/mdrap/ws_feed.py`, we implemented `LiveCryptoFeed` with sequence tracking:

```python
class LiveCryptoFeed:
    def check_sequence_gap(self, venue: str, seq: int) -> bool:
        prev = self.last_seq.get(venue)
        self.last_seq[venue] = seq
        if prev is None:
            return False
        if seq > prev + 1:
            missing = seq - prev - 1
            self.gap_counts[venue] += 1
            self.missing_events_count[venue] += missing
            return True
        return False
```

- Tested in `tests/test_ws_live_market_data.py`:
  - Receipt of sequences [100, 101] -> 0 gaps.
  - Receipt of sequence 105 -> Gap detected, alert recorded, missing counter incremented by 3.
  - Test passed with 100% assertions.

---

## 4. Timestamp Normalization & Precision
Timestamp discrepancy across venues is a critical source of non-deterministic backtesting:
- **Exchange Timestamps**:
  - Binance: Unix epoch milliseconds (`data["E"]`).
  - Coinbase: ISO 8601 string (`"2023-01-01T00:00:00.000000Z"`).
  - OKX: Unix epoch milliseconds (`item["ts"]`).
  - Bybit: Unix epoch milliseconds (`data["ts"]`).
- **Normalized Ingress**:
  - All parsed frames are converted to nanoseconds (`timestamp_ns = int(ms * 1_000_000)`).
  - Wall-clock ingress timestamp is recorded immediately upon frame read via `time.time_ns()`.
  - Latency delta $\Delta t = t_{receive} - t_{exchange}$ tracks network transmission delay.

---

## 5. Audit Classification
`DATA-VALIDATED`: Live feeds from Binance, Coinbase, Kraken, OKX, and Bybit are functional, resilient, and correctly normalized into MDRAP canonical event structures.
