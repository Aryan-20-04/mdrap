# MDRAP REST & WebSocket API Reference

The MDRAP commercial API delivers a high-performance HTTP and WebSocket interface for programmatic consumption of normalized canonical market data, data quality metrics, order book ladders, and audit logs.

- **Base URL**: `http://<host>:<port>/v1`
- **Interactive OpenAPI Specification**: `http://<host>:<port>/docs`
- **Machine-Readable OpenAPI JSON**: `http://<host>:<port>/openapi.json`
- **Protocol**: HTTP/1.1 and WebSockets (RFC 6455)

---

## 1. Authentication & RBAC

All operational endpoints require an active API key supplied in one of two headers:

```http
X-API-Key: <token>
```
or
```http
Authorization: Bearer <token>
```

### Role-Based Access Control (RBAC)

MDRAP enforces hierarchical roles:
- `ADMIN`: Full administrative access (create/revoke keys, register feeds, reprocess quarantine).
- `OPERATOR`: Ingestion and operational control (`POST /v1/ingest`, trigger actions).
- `VIEWER`: Read-only queries (`GET /v1/events`, `GET /v1/bbo`, `GET /v1/quality`, `GET /v1/audit`).

### HTTP Status Codes & Error Format

Errors return standard HTTP status codes and a structured JSON error envelope:

```json
{
  "error": {
    "code": 401,
    "message": "Authentication required: Missing API key in X-API-Key or Authorization header",
    "type": "HTTPException",
    "detail": "Authentication required: Missing API key in X-API-Key or Authorization header"
  },
  "detail": "Authentication required: Missing API key in X-API-Key or Authorization header"
}
```

- `200 OK`: Request succeeded.
- `400 Bad Request`: Malformed payload or validation schema failure.
- `401 Unauthorized`: Missing, invalid, expired, or revoked API key.
- `403 Forbidden`: Authenticated caller possesses insufficient role privilege.
- `404 Not Found`: Target entity (instrument, event, key) does not exist.
- `429 Too Many Requests`: Client exceeded token-bucket rate limits.
- `500 Internal Server Error`: Internal runtime exception (logged to audit trail).
- `503 Service Unavailable`: Platform degraded or engine storage unready.

---

## 2. Complete Route Table

| Method | Endpoint | Min Role | Description |
| --- | --- | --- | --- |
| `GET` | `/v1/health` | *Public* | Platform uptime, engine status, WAL state, and watchdog summary. |
| `GET` | `/v1/liveness` | *Public* | Container liveness probe (`{"status": "alive"}`). |
| `GET` | `/v1/readiness` | *Public* | Readiness probe (`200 ready` or `503` if degraded/unhealthy). |
| `GET` | `/metrics` | *Public/Auth* | Prometheus text exposition format metrics. |
| `POST` | `/v1/ingest` | `OPERATOR` | Batch ingestion of raw market events. |
| `GET` | `/v1/events` | `VIEWER` | Query historical canonical market events. |
| `GET` | `/v1/events/{event_id}` | `VIEWER` | Retrieve a specific canonical event by ID. |
| `GET` | `/v1/instrument/{instrument_id}/latest` | `VIEWER` | Retrieve latest canonical tick for an instrument. |
| `WS` | `/v1/events/stream` | `VIEWER` | Real-time WebSocket canonical event stream. |
| `GET` | `/v1/bbo/{instrument_id}` | `VIEWER` | Query synthetic Best Bid & Offer (NBBO) for an instrument. |
| `GET` | `/v1/depth/{instrument_id}` | `VIEWER` | Query Consolidated Level-2 order book ladder. |
| `GET` | `/v1/quality` | `VIEWER` | Aggregate data quality score and feed reliability metrics. |
| `GET` | `/v1/quarantine` | `VIEWER` | Query quarantined invalid/malformed events. |
| `POST` | `/v1/quarantine/{event_id}/reprocess` | `ADMIN` | Re-evaluate a quarantined event against current rules. |
| `GET` | `/v1/audit` | `VIEWER` | Query tamper-evident cryptographic audit log entries. |
| `GET` | `/v1/audit/verify` | `VIEWER` | Verify SHA-256 Merkle chain integrity of the audit log. |
| `GET` | `/v1/audit/export` | `ADMIN` | Export cryptographic audit trail with signed checkpoints. |
| `GET` | `/v1/feeds` | `VIEWER` | List registered market data feed metadata. |
| `POST` | `/v1/feeds` | `ADMIN` | Register new feed source metadata (`REGISTERED_NOT_RUNNING`). |
| `GET` | `/v1/feed/{source}/health` | `VIEWER` | Retrieve specific feed health and latency statistics. |
| `DELETE` | `/v1/feeds/{feed_id}` | `ADMIN` | Deregister a feed metadata entry. |
| `GET` | `/v1/config` | `VIEWER` | Inspect active platform configuration (secrets redacted). |
| `GET` | `/v1/keys` | `ADMIN` | List active client API entitlements and key IDs. |
| `POST` | `/v1/keys` | `ADMIN` | Issue a new API key and role entitlement. |
| `DELETE` | `/v1/keys/{key_id}` | `ADMIN` | Immediately revoke an API key. |

---

## 3. Verified Endpoint Examples

### 3.1 Health & Diagnostics

#### `GET /v1/health`

```bash
curl -s http://127.0.0.1:8000/v1/health
```

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "3.1.0",
  "uptime_seconds": 342.12,
  "engine": "running",
  "active_feeds_count": 0,
  "db": {
    "path": "data/mdrap.db",
    "wal_mode": true,
    "conflicts": 0
  },
  "shm": {
    "enabled": true,
    "name": "mdrap_feed"
  },
  "watchdog": {
    "healthy_sources": 0,
    "total_monitored": 0,
    "sources": {}
  },
  "persistence_mode": "production_durable",
  "degraded": false,
  "degraded_reason": ""
}
```

#### `GET /metrics`

```bash
curl -s http://127.0.0.1:8000/metrics
```

**Response (200 OK):**
```text
# HELP mdrap_events_processed_total Total market events processed by status
# TYPE mdrap_events_processed_total counter
mdrap_events_processed_total{status="VALID"} 150240
mdrap_events_processed_total{status="SUSPICIOUS"} 42
mdrap_events_processed_total{status="INVALID"} 12
# HELP mdrap_pipeline_latency_seconds Pipeline end-to-end processing latency
# TYPE mdrap_pipeline_latency_seconds summary
mdrap_pipeline_latency_seconds{quantile="0.5"} 0.000012
mdrap_pipeline_latency_seconds{quantile="0.95"} 0.000028
mdrap_pipeline_latency_seconds{quantile="0.99"} 0.000045
```

---

### 3.2 Event Ingestion & Queries

#### `POST /v1/ingest`

Submit one or more raw market events for synchronous validation, normalization, and durable WAL append:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/ingest \
  -H "X-API-Key: $MDRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {
        "source": "BINANCE",
        "payload": {
          "instrument": "BTCUSDT",
          "event_type": "TRADE",
          "exchange_ts": 1700000000.0,
          "sequence": 1042,
          "price": 50125.50,
          "quantity": 0.25
        }
      }
    ]
  }'
```

**Response (200 OK):**
```json
{
  "status": "ok",
  "ingested": 1,
  "canonical": [
    {
      "event_id": "c_BINANCE_1042",
      "instrument_id": "BTCUSDT",
      "event_type": "TRADE",
      "exchange_timestamp": 1700000000.0,
      "receive_timestamp": 1700000000.002,
      "processing_timestamp": 1700000000.003,
      "source": "BINANCE",
      "sequence_number": 1042,
      "price": 50125.50,
      "quantity": 0.25,
      "quality_status": "VALID",
      "reasons": []
    }
  ]
}
```

#### `GET /v1/events`

Query historical canonical market events by symbol:

```bash
curl -s "http://127.0.0.1:8000/v1/events?instrument_id=BTCUSDT&limit=10" \
  -H "X-API-Key: $MDRAP_API_KEY"
```

**Response (200 OK):**
```json
[
  {
    "event_id": "c_BINANCE_1042",
    "instrument_id": "BTCUSDT",
    "event_type": "TRADE",
    "price": 50125.50,
    "quantity": 0.25,
    "exchange_timestamp": 1700000000.0,
    "quality_status": "VALID"
  }
]
```

---

### 3.3 Microstructure: BBO & Market Depth

#### `GET /v1/bbo/{instrument_id}`

Retrieve the latest consolidated Best Bid & Offer:

```bash
curl -s http://127.0.0.1:8000/v1/bbo/BTCUSDT \
  -H "X-API-Key: $MDRAP_API_KEY"
```

**Response (200 OK):**
```json
{
  "instrument_id": "BTCUSDT",
  "best_bid": 50125.00,
  "bid_size": 1.5,
  "bid_venue": "BINANCE",
  "best_ask": 50126.00,
  "ask_size": 2.0,
  "ask_venue": "COINBASE",
  "spread": 1.00,
  "mid_price": 50125.50,
  "timestamp": 1700000000.015,
  "is_locked": false,
  "is_crossed": false
}
```

---

### 3.4 Cryptographic Audit Trail

#### `GET /v1/audit/verify`

Verify the mathematical integrity of the SHA-256 Merkle chain:

```bash
curl -s http://127.0.0.1:8000/v1/audit/verify \
  -H "X-API-Key: $MDRAP_API_KEY"
```

**Response (200 OK):**
```json
{
  "status": "VALID",
  "verified_entries": 45120,
  "head_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "genesis_hash": "0000000000000000000000000000000000000000000000000000000000000000"
}
```

---

### 3.5 API Key Management

#### `POST /v1/keys`

Provision a new client API key:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/keys \
  -H "X-API-Key: $MDRAP_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "quant-trading-desk-1",
    "role": "VIEWER",
    "allowed_symbols": ["BTCUSDT", "ETHUSDT"]
  }'
```

**Response (200 OK):**
```json
{
  "token": "mdrap_token_example_secret",
  "entitlement": {
    "client_id": "quant-trading-desk-1",
    "key_prefix": "mdrap_token_...",
    "key_id": "a1b2c3d4e5f60718",
    "role": "VIEWER",
    "is_active": true,
    "allowed_symbols": ["BTCUSDT", "ETHUSDT"]
  }
}
```

#### `DELETE /v1/keys/{key_id}`

Revoke an API key by its unique `key_id` or hash:

```bash
curl -s -X DELETE http://127.0.0.1:8000/v1/keys/a1b2c3d4e5f60718 \
  -H "X-API-Key: $MDRAP_ADMIN_KEY"
```

**Response (200 OK):**
```json
{
  "status": "revoked",
  "key_id": "a1b2c3d4e5f60718"
}
```

---

## 4. Known Boundaries & Limitations

1. **Feed Supervision**: `POST /v1/feeds` records configuration metadata as `REGISTERED_NOT_RUNNING`. There is no background supervisor that connects or manages external socket network feeds within the API process. Ingestion is driven by calling `POST /v1/ingest` or directly embedding the `mdrap.Engine` Python API.
2. **WebSocket Broadcaster**: `/v1/events/stream` broadcasts events ingested via `POST /v1/ingest`. It is not connected to a live exchange feed adapter by default.
3. **Mandatory Salt**: Running `mdrap serve` outside demo mode requires setting `MDRAP_API_KEY_SALT`. If unset, startup fails closed with a descriptive error.
