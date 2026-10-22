# REST & WebSocket API Reference

The MDRAP API server (`mdrap serve`) exposes HTTP endpoints and WebSocket streaming channels for market data ingestion, querying, feed monitoring, and cryptographic audit verification.

All `/v1/` endpoints except public health routes require bearer authentication:
```http
Authorization: Bearer <API_TOKEN>
```

---

## 1. System Health & Readiness (Unauthenticated)

| Method | Endpoint | Access | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/health` | Public | Comprehensive engine status, uptime, and database metrics. |
| `GET` | `/v1/liveness` | Public | Lightweight container liveness probe (`{"status": "alive"}`). |
| `GET` | `/v1/readiness` | Public | Verifies SQLite read/write and WAL directory accessibility. |
| `GET` | `/metrics` | Public / Operator | Prometheus formatted telemetry counters, gauges, and histograms. |

---

## 2. Ingestion Endpoints

### `POST /v1/ingest`
Ingests a single raw market data event synchronously.
- **Required Role**: `OPERATOR` or `ADMIN`
- **Request Body**:
```json
{
  "source": "SIM_FEED",
  "payload": {
    "instrument": "AAPL",
    "event_type": "QUOTE",
    "exchange_ts": 1700000000.0,
    "bid": 150.25,
    "ask": 150.30,
    "sequence": 1001
  }
}
```
- **Response `200 OK`**:
```json
{
  "event_id": "ev_019283a0",
  "status": "VALID",
  "quality_flags": 0,
  "reasons": []
}
```

### `POST /v1/ingest/batch`
Ingests a batch of raw market events in a single transactional SQLite and WAL block.
- **Required Role**: `OPERATOR` or `ADMIN`
- **Request Body**: Array of `RawEvent` objects.

---

## 3. Real-Time WebSocket Streaming

### `WebSocket /v1/events/stream`
Subscribes to live canonical events ingested by the platform.
- **Query Parameters**:
  - `symbol` (optional): Filter events by ticker symbol (e.g. `?symbol=BTC-USDT`).
  - `status` (optional): Filter by quality status (`VALID`, `SUSPICIOUS`).
- **Authorization**: Pass token in query string `?token=<TOKEN>` or initial connection headers.

> [!NOTE]
> The WebSocket stream emits events ingested via `/v1/ingest` or the local gateway socket. It does not spawn independent connections to external cryptocurrency exchanges.

---

## 4. Market Data Query Endpoints

| Method | Endpoint | Required Role | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/bbo/{instrument_id}` | `VIEWER` | Returns the current consolidated Best Bid and Offer (BBO). |
| `GET` | `/v1/depth/{instrument_id}` | `VIEWER` | Returns the aggregated L2 order book depth levels. |
| `GET` | `/v1/events` | `VIEWER` | Paginated query of canonical events with optional symbol/timestamp filters. |
| `GET` | `/v1/events/{event_id}` | `VIEWER` | Fetch a single canonical event by unique event ID. |

---

## 5. Quality & Quarantine Endpoints

| Method | Endpoint | Required Role | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/quality` | `VIEWER` | Aggregated statistical quality counters and rule violation metrics. |
| `GET` | `/v1/quarantine` | `VIEWER` | Lists quarantined events that failed quality validation checks. |
| `POST` | `/v1/quarantine/{event_id}/reprocess` | `ADMIN` | Forces re-evaluation of a quarantined event under relaxed rules. |

---

## 6. Feed Metadata & Monitoring

| Method | Endpoint | Required Role | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/feeds` | `VIEWER` | Lists all registered feed metadata and last-seen timestamps. |
| `POST` | `/v1/feeds` | `ADMIN` | Registers a new feed source metadata (`REGISTERED_NOT_RUNNING`). |
| `GET` | `/v1/feed/{source}/health` | `VIEWER` | Health, latency jitter, and packet drop stats for a feed source. |

---

## 7. Cryptographic Audit Endpoints

| Method | Endpoint | Required Role | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/v1/audit` | `VIEWER` | Returns recent audit log entries with previous/entry hashes. |
| `GET` | `/v1/audit/verify` | `OPERATOR` | Cryptographically recalculates and validates the entire SHA-256 Merkle chain. |
| `GET` | `/v1/audit/export` | `ADMIN` | Exports a signed, encrypted zip file containing complete audit lineage. |

---

## 8. Client Entitlements & Keys

| Method | Endpoint | Required Role | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/keys` | `ADMIN` | Generates a new API token with assigned role (`VIEWER`, `OPERATOR`, `ADMIN`). |
| `GET` | `/v1/keys` | `ADMIN` | Lists registered API keys (tokens are redacted, showing key ID and prefix). |
| `DELETE` | `/v1/keys/{key_id}` | `ADMIN` | Revokes an active API key immediately. |
