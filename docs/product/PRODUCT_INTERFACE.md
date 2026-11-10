# MDRAP Operational Reliability Interface & API Specification

**Document Version**: 2.0  
**Responsible Agent**: Agent 7 (Product API, Dashboard & Developer Experience)  
**Status**: VERIFIED IMPLEMENTATION  

---

## 1. Interface Philosophy: Operational Reliability, Not a Retail Terminal

MDRAP deliberately rejects the generic trading terminal paradigm. It does not attempt to display retail candlestick graphs, technical indicators (RSI/MACD), or order placement forms.

Instead, MDRAP provides an **Operational Reliability Cockpit** designed for market-data engineers, quantitative developers, and risk managers:

1. **Overview Cockpit**: Real-time multi-venue feed health, packet ingestion rates, data freshness, sequence gaps, backpressure drops, and WAL persistence state.
2. **Incident Explorer**: Active and historical anomaly episodes with affected instruments, severity ratings, root-cause diagnostics, and one-click deterministic replay triggers.
3. **Evidence Explorer**: Cryptographic provenance viewer linking canonical events to original raw wire payloads, SHA-256 digests, and RFC 6962 Merkle audit chains.

---

## 2. Terminal Operational Cockpit

Accessible directly via the MDRAP CLI:

```powershell
# 1. Platform status & active incident monitor
python cli.py status --db data/mdrap.db

# 2. Live assurance multi-venue stream inspector
python cli.py live BTC/USD --ws -l 20

# 3. Deterministic incident replay & forensic proof
python cli.py replay --incident <INCIDENT_ID> --db data/mdrap.db

# 4. Cryptographic audit chain verification
python cli.py audit --verify --db data/mdrap.db
```

---

## 3. Read-Only Assurance HTTP & WebSocket API

MDRAP exposes a clean, read-only REST & WebSocket API powered by FastAPI/Uvicorn (`src/mdrap/api.py`):

### 3.1 Feed Health & Telemetry Endpoint
`GET /api/v1/assurance/health`

**Response (`200 OK`)**:
```json
{
  "status": "HEALTHY",
  "providers": {
    "BINANCE": {
      "state": "CONNECTED",
      "active_subscriptions": ["BTC/USD", "ETH/USD"],
      "feed_freshness_ms": 14.2,
      "reconnect_count": 0,
      "error_count": 0,
      "sequence_gaps_count": 0,
      "wire_latency_us": 271.5,
      "data_mode": "LIVE"
    },
    "BYBIT": {
      "state": "DEGRADED",
      "active_subscriptions": ["BTC/USD"],
      "feed_freshness_ms": 18.9,
      "reconnect_count": 1,
      "error_count": 0,
      "sequence_gaps_count": 19,
      "wire_latency_us": 416.1,
      "data_mode": "LIVE"
    }
  }
}
```

### 3.2 Incident Explorer Endpoint
`GET /api/v1/assurance/incidents?limit=10&provider=BYBIT`

**Response (`200 OK`)**:
```json
[
  {
    "incident_id": "inc-1794334613-0001",
    "provider": "BYBIT",
    "instrument": "BTC/USD",
    "anomaly_type": "SEQUENCE_GAP",
    "severity": "HIGH",
    "start_timestamp": 1794334613.53,
    "end_timestamp": 1794334613.54,
    "event_ids": ["evt-77f621-1"],
    "impact_summary": "Bybit sequence jump of 19 ticks detected on live stream",
    "is_active": true,
    "reproduced": true,
    "reproduction_hash": "8fd90a6ddf40a57349c8830a20506b5cbba277be8308f5fe72df3fec47ada530"
  }
]
```

### 3.3 Event Provenance Evidence Endpoint
`GET /api/v1/assurance/provenance/{event_id}`

**Response (`200 OK`)**:
```json
{
  "evidence_type": "MARKET_DATA_ASSURANCE_PROVENANCE",
  "event_id": "evt-77f621-1",
  "provider": "BYBIT",
  "instrument": "BTC/USD",
  "channel": "ORDERBOOK_L1",
  "data_mode": "LIVE",
  "timestamps": {
    "exchange_ts": 1794334623.769,
    "receive_ts": 1794334613.530,
    "processing_ts": 1794334613.532,
    "clock_drift_ms": -10239.0
  },
  "sequence_number": 117147166309,
  "quality": {
    "status": "SUSPICIOUS",
    "reasons": ["SEQUENCE_GAP", "TS_IMPLAUSIBLE"],
    "processing_status": "PROCESSED"
  },
  "integrity": {
    "payload_sha256": "4b6c8a7e...",
    "lineage_hash": "a1f89c02...",
    "durability_status": "DURABLE_DISK"
  },
  "verification_status": "VERIFIED_TAMPER_EVIDENT"
}
```

### 3.4 Deterministic Incident Replay Trigger
`POST /api/v1/assurance/replay/{incident_id}`

**Response (`200 OK`)**:
```json
{
  "incident_id": "inc-1794334613-0001",
  "instrument_id": "BTC/USD",
  "provider": "BYBIT",
  "events_replayed": 1,
  "is_reproducible": true,
  "reproduced_anomalies": ["SEQUENCE_GAP", "TS_IMPLAUSIBLE"],
  "initial_bbo": {"best_bid": 73244.80, "best_ask": 73244.90},
  "final_bbo": {"best_bid": 73244.80, "best_ask": 73244.90},
  "replayed_state_hash": "8fd90a6ddf40a57349c8830a20506b5cbba277be8308f5fe72df3fec47ada530"
}
```
