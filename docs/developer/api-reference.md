# REST & WebSocket API Reference

MDRAP exposes an institutional, read-only HTTP REST and WebSocket API (`mdrap.api`) designed for sidecar integration with trading desks, risk monitors, and internal compliance dashboards.

---

## 1. Authentication & Security

- **Local Loopback Isolation**: In standard deployments, the API binds strictly to `127.0.0.1:8000`.
- **API Key Authentication**: When enabled, endpoints require an `X-API-Key` HTTP header.
- **Salt Requirement**: API keys are hashed with a unique, cryptographically random salt (`MDRAP_API_KEY_SALT`).
- **Read-Only Invariant**: All mutating trading actions (order routing, cancellation, execution) are completely absent.

---

## 2. Health & Telemetry Endpoints

### `GET /health` or `GET /v1/health`
Returns the operational health status of the sidecar engine, storage WAL, and connected feeds.

**Response**:
```json
{
  "status": "healthy",
  "version": "6.0.0",
  "uptime_seconds": 3840.2,
  "wal_mode": true,
  "feeds_active": 2,
  "events_processed": 142080
}
```

### `GET /metrics`
Exposes Prometheus-compatible metrics for scraping:
- `mdrap_events_total{status="VALID|SUSPICIOUS|INVALID"}`
- `mdrap_feed_latency_ms{provider="COINBASE|KRAKEN"}`
- `mdrap_sequence_gaps_total{provider="..."}`
- `mdrap_storage_wal_size_bytes`

---

## 3. Market Data & Quality Endpoints

### `GET /v1/events`
Query normalized canonical events.
- Query parameters:
  - `instrument`: Filter by symbol (e.g. `BTC-USD`)
  - `provider`: Filter by source venue (e.g. `COINBASE`)
  - `limit`: Maximum events to return (default: `100`, max: `1000`)
  - `status`: Filter by `VALID`, `SUSPICIOUS`, or `INVALID`

### `GET /v1/bbo/{instrument_id}`
Get the latest consolidated or venue-specific Best Bid and Offer (BBO) quote.

**Response**:
```json
{
  "instrument_id": "BTC-USD",
  "best_bid": 65200.0,
  "best_ask": 65200.5,
  "spread": 0.5,
  "spread_bps": 0.77,
  "is_crossed": false,
  "timestamp": 1700000000.123
}
```

### `GET /v1/depth/{instrument_id}`
Returns the current Level 2 order book snapshot up to requested depth.

### `GET /v1/quality`
Returns aggregate data-quality statistics and rule violation counters across all active feeds.

### `GET /v1/quarantine`
Lists events quarantined by the Quality Engine due to structural or microstructure violations.

---

## 4. Audit & Provenance Endpoints

### `GET /v1/audit`
Returns recent Merkle tree batch roots and timestamp anchors.

### `GET /v1/audit/verify?event_id={id}`
Returns the cryptographic proof of inclusion for a specific canonical event, containing sibling hashes and the active RFC 6962 root.

### `GET /v1/audit/export?incident_id={id}`
Exports a self-contained forensic evidence package conforming to the external assurance schema for compliance audits.

---

## 5. Streaming WebSocket API

### `WS /v1/events/stream`
Publishes a real-time stream of validated canonical market events and quarantine alerts.

**Connection URL**:
```text
ws://127.0.0.1:8000/v1/events/stream?instrument=BTC-USD
```

**Message Format**:
```json
{
  "type": "CANONICAL_TICK",
  "event_id": "evt-cd75491fa9b66a44-10",
  "provider": "COINBASE",
  "instrument": "BTC-USD",
  "bid": 65200.0,
  "ask": 65200.5,
  "quality_status": "VALID",
  "lineage_hash": "240057078e6226daf49c78be8c881c4ca8cf28ba0bf15488e7386f52799912a7"
}
```
