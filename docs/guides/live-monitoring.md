# Live Monitoring Guide

## Goal
Connect to a running MDRAP daemon or API service, subscribe to real-time validated canonical market events, and monitor feed health, BBO updates, and drop counters.

## Prerequisites
- Python 3.10+
- Running MDRAP daemon (`MarketDataDaemon` on port `9988`) or API server (`mdrap serve` on port `8000`)
- `MDRAP_API_KEY_SALT` configured in your environment

## Steps

### Step 1: Start the Market Data Service
Start the local streaming daemon via Python or CLI:

```bash
export MDRAP_API_KEY_SALT="salt-must-be-min-16-chars-long"
export MDRAP_INITIAL_ADMIN_KEY="admin-token"

# Start the REST/WebSocket API
mdrap serve --host 127.0.0.1 --port 8000 --db ./data/mdrap.db
```

### Step 2: Subscribe to Live Canonical Ticks
Use `StreamClient` to connect to the daemon's TCP stream socket, or use WebSockets for HTTP clients:

<!-- doc-test: python -->
```python
# doc-test
import os, threading, time
from mdrap.service import MarketDataDaemon, StreamClient

# Provide required cryptographic salt
os.environ["MDRAP_API_KEY_SALT"] = "live_monitoring_guide_salt_16"

# Run an ephemeral test daemon
daemon = MarketDataDaemon(host="127.0.0.1", port=9986, db_path=":memory:")
t = threading.Thread(target=daemon.start, daemon=True)
t.start()
time.sleep(0.3)

client = StreamClient(host="127.0.0.1", port=9986)
health = client.get_health()
print(f"Monitored Feeds: {sorted(list(health.keys()))}")

client.close()
daemon.stop()
```

### Step 3: Stream Live Updates via WebSocket
To stream canonical events over WebSockets from the API service:

```bash
# Ingest an event via REST
curl -X POST http://127.0.0.1:8000/v1/ingest \
  -H "Authorization: Bearer admin-token" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "SIM_VENUE",
    "payload": {
      "instrument": "AAPL",
      "event_type": "QUOTE",
      "exchange_ts": 1700000000.0,
      "bid": 150.25,
      "ask": 150.30
    }
  }'
```

Connect a WebSocket client to `ws://127.0.0.1:8000/v1/events/stream` with the header `Authorization: Bearer admin-token` to receive the published event.

## Expected Output
```text
Monitored Feeds: ['FEEDX', 'FEEDY', 'FEEDZ']
```

## Troubleshooting
- **Connection Refused**: Verify that the daemon or API server is running on the expected host/port. Test connectivity using `curl http://127.0.0.1:8000/v1/health` or `telnet 127.0.0.1 9988`.
- **401 Unauthorized**: Ensure the `Authorization: Bearer <token>` header matches an active token generated under the current `MDRAP_API_KEY_SALT`.
- **Drops under Backpressure**: If downstream subscribers fall behind, check `_drop_count` in daemon stats. Consider increasing client buffer sizing or using the zero-copy shared memory (`mdrap.shm`) ring buffer for local IPC.

## Next Steps
- Learn how to deploy MDRAP in high-availability environments in the [Operations Guide](operations.md).
- Inspect REST and WebSocket schemas in the [API Reference](../reference/api.md).
