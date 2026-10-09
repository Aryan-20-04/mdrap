# MDRAP Phase 8 — Feed Conformance Matrix

## 1. Conformance Matrix Overview
This matrix validates that all implemented feed adapters satisfy MDRAP's canonical schema, normalization invariants, clock handling, and error quarantine requirements:

| Adapter | Wire Protocol | Timestamp Resolution | Monotonic Sequence | Schema Normalization | Error Quarantine (INV-01) |
|---|---|---|---|---|---|
| **Nasdaq ITCH 5.0** | Binary UDP/TCP | Nanoseconds (GPS epoch) | Monotonic 64-bit int | Full L3 Order Book Map | Malformed frames logged to quarantine |
| **Databento DBN** | Binary TCP | Nanoseconds (UNIX epoch) | Monotonic 64-bit seq | Direct Trade/BBO mapping | Frame corruption quarantined |
| **Polygon.io** | JSON WebSocket | Milliseconds / Nanoseconds | Sequence number optional | Maps `T` (trade), `Q` (quote) | Schema errors caught & counted |
| **Coinbase Advanced** | JSON WebSocket | ISO-8601 Microseconds | Sequence per product | Level2 / Ticker normalization | Null field fallback to `GATEWAY_RECV` |
| **Binance Depth** | JSON WebSocket | Milliseconds (UNIX) | Sequence per symbol | Order book snapshot/delta | Null sequence permitted |
| **Kraken Ticker** | JSON WebSocket | Seconds float | Channel ID seq | Bid/Ask/Last Trade map | Malformed payloads quarantined |

## 2. Replay-to-Live Parity
MDRAP's feed architecture enforces deterministic replay:
- Processing captured binary PCAP streams through `ITCHParser` yields identical canonical sequences as live multicast decoding.
- Fixed seed test runs (`tests/test_itch.py`) verify 100% bit-for-bit replay parity.
