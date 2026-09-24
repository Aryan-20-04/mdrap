# MDRAP REST & WebSocket API Reference

The MDRAP commercial API delivers a high-performance HTTP and WebSocket interface for programmatic consumption of normalized canonical market data, data quality metrics, order book ladders, and audit logs.

- **Base URL**: `http://<host>:<port>/v1`
- **Interactive OpenAPI Specification**: `http://<host>:<port>/docs`
- **Protocol**: HTTP/1.1 and WebSockets (RFC 6455)

---

## 1. Authentication & Headers

All protected endpoints require an active API key supplied in one of two formats:

```http
X-API-Key: mdrap_live_<token>
```
or
```http
Authorization: Bearer mdrap_live_<token>
```

### HTTP Status Codes
- `200 OK`: Request succeeded.
- `400 Bad Request`: Malformed parameters or schema validation failure.
- `401 Unauthorized`: Missing, invalid, expired, or revoked API key.
- `403 Forbidden`: Authenticated client possesses insufficient RBAC role.
- `404 Not Found`: Target entity (instrument, feed, key) does not exist.
- `429 Too Many Requests`: Client exceeded token bucket rate limits.
- `500 Internal Server Error`: Server exception (details logged to audit log).

---

## 2. Endpoints Reference

### 2.1 Health & Telemetry

#### `GET /v1/health`
- **Required Role**: None (Public healthcheck endpoint for load balancers and container monitors).
- **Description**: Returns platform runtime status, engine uptime, database WAL state, SHM status, and source watchdog summary.
- **Example Response**:
  ```json
  {
    "status": "healthy",
    "version": "2.2.0",
    "uptime_seconds": 124.5,
    "engine": "running",
    "active_feeds_count": 3,
    "db": {
      "path": "/data/mdrap.db",
      "wal_mode": true,
      "conflicts": 0
    },
    "shm": {
      "enabled": true,
      "name": "mdrap_feed"
    },
    "watchdog": {
      "healthy_sources": 3,
      "total_monitored": 3,
      "sources": {
        "POLYGON": "HEALTHY",
        "BINANCE": "HEALTHY",
        "COINBASE": "HEALTHY"
      }
    }
  }
  ```

#### `GET /metrics`
- **Required Role**: Loopback bypass (`127.0.0.1`, `::1`, `localhost`) by default; requires active API key when `MDRAP_METRICS_AUTH=1` for external callers.
- **Description**: Exposes Prometheus-compatible text exposition format (`version=0.0.4`) containing platform health gauges, ingest/quarantine counters, latency histograms, and buffer occupancy watermarks.
- **Security & Reverse Proxy Hardening**:
  - Direct TCP peer host is validated. Untrusted client headers like `X-Forwarded-For` are ignored by default.
  - To enable reverse-proxy IP resolution (e.g. behind AWS ALB, NGINX, or Caddy), configure trusted upstream proxies via `MDRAP_TRUSTED_PROXY_IPS="10.0.0.1,10.0.0.2"`. Only requests arriving from these explicit IP addresses will have their `X-Forwarded-For` header inspected.
- **Sample Output**:
  ```text
  # HELP mdrap_events_processed_total Total market events ingested and processed
  # TYPE mdrap_events_processed_total counter
  mdrap_events_processed_total{status="VALID"} 1420580
  mdrap_events_processed_total{status="SUSPICIOUS"} 284
  mdrap_events_processed_total{status="INVALID"} 15
  # HELP mdrap_shm_buffer_watermark_warning Current SHM ring buffer watermark warning flag state
  # TYPE mdrap_shm_buffer_watermark_warning gauge
  mdrap_shm_buffer_watermark_warning 0
  ```

---

### 2.2 Feed Management

#### `GET /v1/feeds`
- **Required Role**: `VIEWER`
- **Description**: Returns all registered data feeds, their active status, monitored symbols, and event counters.

#### `POST /v1/feeds`
- **Required Role**: `ADMIN`
- **Description**: Dynamically register and configure an inbound market feed.
- **Request Body**:
  ```json
  {
    "source": "POLYGON_EQUITIES",
    "provider": "polygon",
    "symbols": ["AAPL", "MSFT", "NVDA"],
    "secret": "optional_pre_shared_hmac_secret"
  }
  ```

#### `DELETE /v1/feeds/{source}`
- **Required Role**: `ADMIN`
- **Description**: Immediately blocks and deactivates a feed source in the watchdog and reconciler.

---

### 2.3 Market Data & Events

#### `GET /v1/events`
- **Required Role**: `VIEWER`
- **Query Parameters**:
  - `instrument_id` (string, optional): Filter by symbol ticker (e.g. `AAPL`, `BTC/USD`).
  - `limit` (integer, default `100`, max `1000`): Maximum events to return.
  - `since_ts` (float, optional): Filter events with `exchange_timestamp >= since_ts`.
  - `status` (string, optional): `VALID` or `SUSPICIOUS`.
- **Response**: Array of normalized canonical event records.

#### `GET /v1/bbo/{instrument_id}`
- **Required Role**: `VIEWER`
- **Description**: Retrieves the real-time National Best Bid and Offer (NBBO) for an instrument across all active market feeds.
- **Example Response**:
  ```json
  {
    "instrument_id": "BTC/USD",
    "bbo": {
      "instrument_id": "BTC/USD",
      "best_bid": 65120.00,
      "best_bid_size": 2.45,
      "best_bid_source": "BINANCE",
      "best_ask": 65121.50,
      "best_ask_size": 1.80,
      "best_ask_source": "COINBASE",
      "spread": 1.50,
      "mid_price": 65120.75,
      "is_crossed": false,
      "is_locked": false,
      "timestamp": 1700000000.123
    }
  }
  ```

#### `GET /v1/depth/{instrument_id}`
- **Required Role**: `VIEWER`
- **Description**: Retrieves the consolidated L2 order book depth ladder across all venues.
- **Example Response**:
  ```json
  {
    "instrument_id": "BTC/USD",
    "depth": {
      "instrument_id": "BTC/USD",
      "bids": [
        [65120.00, 2.45],
        [65119.50, 5.10],
        [65118.00, 10.00]
      ],
      "asks": [
        [65121.50, 1.80],
        [65122.00, 4.20],
        [65125.00, 8.50]
      ],
      "timestamp": 1700000000.125
    }
  }
  ```

---

### 2.4 Data Quality & Quarantine

#### `GET /v1/quality`
- **Required Role**: `VIEWER`
- **Description**: Summarizes real-time data reliability metrics, consensus agreement scores, cross-feed anomaly rates, and feed health rankings.

#### `GET /v1/quarantine`
- **Required Role**: `OPERATOR`
- **Query Parameters**:
  - `limit` (integer, default `100`, max `1000`): Maximum quarantined records.
  - `reason` (string, optional): Specific anomaly filter (e.g. `PRICE_ANOMALY`, `CROSSED_QUOTE`, `STALE`, `SCHEMA_VIOLATION`).
- **Description**: Inspects quarantined invalid market events preserved for audit and post-trade compliance.

---

### 2.5 Cryptographic Audit Trail

#### `GET /v1/audit`
- **Required Role**: `OPERATOR`
- **Query Parameters**:
  - `limit` (integer, default `100`, max `1000`)
  - `action` (string, optional)
- **Description**: Retrieves recent append-only audit log entries.

#### `GET /v1/audit/verify`
- **Required Role**: `OPERATOR`
- **Description**: Cryptographically verifies the unbroken SHA-256 Merkle chain from genesis to the current tip.
- **Example Response**:
  ```json
  {
    "verified": true,
    "entries_checked": 1420,
    "message": "Cryptographic audit chain verified (1420 entries intact)",
    "timestamp": 1700000000.55
  }
  ```

#### `GET /v1/audit/export`
- **Required Role**: `OPERATOR`
- **Description**: Exports a self-contained, cryptographically signed JSON audit bundle for independent compliance auditing.

---

### 2.6 Key Management (ADMIN Only)

#### `GET /v1/keys`
- **Required Role**: `ADMIN`
- **Description**: Lists all client API credentials. Returns masked prefixes (`key_prefix`), roles, and creation timestamps. Raw secret tokens are never returned.

#### `POST /v1/keys`
- **Required Role**: `ADMIN`
- **Request Body**:
  ```json
  {
    "client_id": "TradingDesk_Tokyo",
    "role": "OPERATOR",
    "rate_limit_eps": 50000.0
  }
  ```
- **Response**: Returns the raw secret token **EXACTLY ONCE**:
  ```json
  {
    "token": "mdrap_live_8F-9djk2m...",
    "key_prefix": "mdrap_live_8...",
    "client_id": "TradingDesk_Tokyo",
    "role": "OPERATOR",
    "created_at": 1700000000.0,
    "message": "Store this API key securely. It will not be shown again."
  }
  ```

#### `DELETE /v1/keys/{key_prefix}`
- **Required Role**: `ADMIN`
- **Description**: Immediately revokes an API key by prefix or token hash.

---

## 3. Real-Time WebSocket Streaming

- **URL**: `ws://<host>:<port>/v1/events/stream?token=<api_key>`
- **Sub-Protocol**: JSON stream

### Authentication
Provide the API key via the URL query parameter:
```text
ws://localhost:8000/v1/events/stream?token=mdrap_live_...
```
Upon connection, the server transmits a confirmation handshake:
```json
{
  "type": "ACK",
  "message": "Connected to MDRAP Stream. Role: VIEWER",
  "client_id": "Tokyo_Desk"
}
```

### Commands

#### Subscribe to Symbols:
```json
{
  "action": "SUB",
  "symbols": ["BTC/USD", "AAPL"]
}
```
Response:
```json
{
  "type": "SUBSCRIPTION_UPDATE",
  "subscribed": ["BTC/USD", "AAPL"]
}
```

#### Unsubscribe from Symbols:
```json
{
  "action": "UNSUB",
  "symbols": ["AAPL"]
}
```

#### Keep-Alive Ping:
```json
{
  "action": "PING"
}
```
Response:
```json
{
  "type": "PONG",
  "timestamp": 1700000000.123
}
```

#### Streaming Tick Frame:
```json
{
  "type": "CANONICAL_TICK",
  "instrument_id": "BTC/USD",
  "price": 65120.50,
  "quantity": 1.25,
  "source": "BINANCE",
  "quality_status": "VALID",
  "exchange_timestamp": 1700000000.120,
  "canonical_timestamp": 1700000000.121
}
```
