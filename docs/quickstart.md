# MDRAP Quickstart Guide

This guide walks through initializing MDRAP's in-process canonical engine and optional HTTP/WebSocket API service.

> [!NOTE]
> Server feed registration (`POST /v1/feeds`) registers feed metadata only (`REGISTERED_NOT_RUNNING`). The server WebSocket is not connected to a feed runtime by default; ingestion is driven via `POST /v1/ingest`, the gateway daemon, or direct Python scripts.

---

## 1. Prerequisites & Installation

- Python 3.10 or newer
- Core installation has zero mandatory external dependencies.
- Optional dependencies: `[api]` for FastAPI/Uvicorn, `[ui]` for Rich terminal dashboards.

```bash
# Clone and enter the repository
git clone https://github.com/mdrap-org/mdrap.git
cd mdrap

# Core engine installation (pure Python stdlib)
python -m pip install -e .

# Full installation with API service
python -m pip install -e ".[api]"
```

---

## 2. Process an Event with Canonical Engine

The high-level `Engine` provides synchronous WAL durability, data normalization, quality evaluation, and indexed querying:

<!-- doc-test: python -->
```python
# doc-test
import tempfile, time
from mdrap import Engine, EngineConfig, RawEvent

wal_dir = tempfile.mkdtemp()
engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

now = time.time()
raw = RawEvent(
    source="BATS",
    payload={
        "instrument": "AAPL",
        "event_type": "QUOTE",
        "bid": 150.25,
        "ask": 150.30,
        "exchange_ts": now,
        "sequence": 1,
    },
    receive_timestamp=now,
)

decision = engine.submit(raw)
canonical = decision.canonical_event

print(f"Status: {canonical.quality_status.name}")
print(f"BBO: {canonical.bid_price} / {canonical.ask_price}")
ticks = engine.query("AAPL", limit=5)
print(f"Queried ticks: {len(ticks)}")
engine.close()
```

Expected output:
```text
Status: VALID
BBO: 150.25 / 150.3
Queried ticks: 1
```

---

## 3. Running the REST & WebSocket API Server

To run the API server outside demo mode, configure a cryptographic salt (minimum 16 characters) and an initial admin key:

```bash
# Generate and set required cryptographic salt
export MDRAP_API_KEY_SALT=$(python -c "import secrets; print(secrets.token_hex(16))")
export MDRAP_INITIAL_ADMIN_KEY="admin-secret-token"

# Start the server (binds to localhost by default)
mdrap serve --host 127.0.0.1 --port 8000 --db ./data/mdrap.db
```

Verify service liveness and readiness:

```bash
curl -s http://127.0.0.1:8000/v1/health
curl -s http://127.0.0.1:8000/v1/readiness
```

Ingest a sample market event via HTTP:

```bash
curl -X POST http://127.0.0.1:8000/v1/ingest \
  -H "Authorization: Bearer admin-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "SIM_VENUE",
    "payload": {
      "instrument": "BTC-USDT",
      "event_type": "TRADE",
      "exchange_ts": 1700000000.0,
      "sequence": 1,
      "price": 65000.0,
      "quantity": 0.5
    }
  }'
```

---

## 4. Running the Verification Suite

Run the full core test suite:

```bash
pytest tests/ -q
```

Or execute the complete 9-stage quality protocol:

```bash
python scripts/check.py
```
