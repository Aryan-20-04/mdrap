# Market Data Connectors

MDRAP includes passive, read-only connectors for premier institutional and retail crypto and multi-asset exchanges.

---

## 1. Connector Design Principles

1. **Read-Only Invariant**: All connectors connect exclusively to public WebSocket streams or multicast data feeds. They never request, store, or transmit API keys, secrets, or trading passphrases.
2. **Reconnection & Exponential Backoff**: Automatic reconnection with exponential backoff and jitter prevents reconnect storms during exchange outages.
3. **Heartbeat & Telemetry**: Continuous tracking of ping/pong round-trip times, clock drift against host monotonic time, and frame ingestion rates.
4. **Sequence Number Continuity**: Monitors exchange-assigned sequence numbers to detect packet drops immediately.

---

## 2. Supported Providers

### A. Coinbase Exchange (`mdrap.ws_feed`)
- **Protocol**: Secure WebSocket (`wss://ws-feed.exchange.coinbase.com`)
- **Supported Channels**:
  - `ticker`: Real-time best bid and ask (BBO) and last trade.
  - `level2` / `level2_batch`: Top-of-book and incremental depth updates.
- **Sequence Tracking**: Coinbase embeds an integer `sequence` field in every message; gaps trigger automated alerts.

### B. Kraken (`mdrap.ws_feed`)
- **Protocol**: Secure WebSocket (`wss://ws.kraken.com`)
- **Supported Channels**:
  - `ticker`: Bid, ask, volume, and 24h weighted metrics.
  - `book-10` / `book-25`: Level 2 order book snapshot and delta feeds.
- **Timestamp Precision**: Kraken provides microsecond exchange timestamps.

### C. Binance Spot & Futures
- **Protocol**: Secure WebSocket (`wss://stream.binance.com:9443/ws`)
- **Supported Channels**:
  - `bookTicker`: Ultra-fast best bid and ask updates.
  - `depth5@100ms`: Top 5 price levels pushed at 100ms intervals.
- **Sequence Tracking**: Uses `u` (final update ID) and `U` (first update ID) to detect missing book deltas.

### D. Databento / SBE Feeds (Optional)
- **Protocol**: Simple Binary Encoding (SBE) / DBN format.
- **Decoding**: Accelerated via native C SBE parser (`src/fastpath.c`) for sub-microsecond binary unpacking.

---

## 3. Configuring Connectors

In `config.toml`:

```toml
[providers.coinbase]
enabled = true
ws_url = "wss://ws-feed.exchange.coinbase.com"
channels = ["ticker", "level2"]
instruments = ["BTC-USD", "ETH-USD"]
read_only = true

[providers.kraken]
enabled = true
ws_url = "wss://ws.kraken.com"
channels = ["ticker"]
instruments = ["XBT/USD"]
read_only = true
```

---

## 4. Telemetry Metrics

MDRAP tracks real-time connector health metrics exposed via the CLI and REST API:

- `frames_ingested`: Cumulative raw frames received.
- `bytes_ingested`: Total wire bytes consumed.
- `clock_drift_ms`: Difference between exchange timestamp and host receive timestamp.
- `sequence_gaps_detected`: Number of detected packet drop incidents.
- `last_heartbeat_s`: Seconds since the last valid payload was received.
- `reconnect_count`: Total reconnection attempts executed.
