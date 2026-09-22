# MDRAP Python SDK Guide

The MDRAP Python SDK (`MDRAPClient`) provides a clean, unified interface for interacting with the MDRAP platform across all supported transports:
- **REST & WebSockets** (Standard distributed client access)
- **Zero-Copy Shared Memory (SHM)** (Sub-microsecond local-host access)
- **High-Performance Binary Wire Protocol** (Ultra-low latency over TCP)

The SDK utilizes standard library components (`urllib.request`) for HTTP, requiring **zero external dependencies** for basic integration.

---

## 1. Quick Example: REST Client

```python
from client import MDRAPClient

with MDRAPClient(base_url="http://localhost:8000", api_key="mdrap_live_...") as client:
    # 1. Inspect platform health
    health = client.health()
    print(f"MDRAP Health: {health['status']} | Active Feeds: {health['active_feeds_count']}")

    # 2. Query historical canonical events
    events = client.query_events(instrument_id="AAPL", limit=10)
    for e in events:
        print(f"[{e['canonical_timestamp']}] {e['instrument_id']} @ {e['price']}")

    # 3. Query Real-Time Consolidated NBBO
    bbo = client.get_bbo("BTC/USD")
    print(f"NBBO: Bid {bbo.get('best_bid')} / Ask {bbo.get('best_ask')}")

    # 4. Verify Cryptographic Merkle Audit Trail
    audit = client.verify_audit()
    print(f"Merkle Chain Integrity: {audit['message']}")
```

---

## 2. Real-Time WebSocket Event Streaming

The SDK includes a built-in generator for streaming live canonical ticks and quotes:

```python
from client import MDRAPClient

client = MDRAPClient(base_url="http://localhost:8000", api_key="mdrap_live_...")

# Stream ticks for selected instruments
print("Streaming real-time canonical market events...")
for event in client.stream_events(symbols=["BTC/USD", "ETH/USD"]):
    print(f"[{event['source']}] {event['instrument_id']} Price: {event['price']} (Status: {event.get('quality_status', 'VALID')})")
```

---

## 3. Administrative Workflows

```python
from client import MDRAPClient

# Connect with ADMIN privileges
with MDRAPClient(base_url="http://localhost:8000", api_key="mdrap_live_admin_token") as admin:
    # 1. Register a new direct market feed
    admin.register_feed(
        source="POLYGON_CRYPTO",
        provider="polygon",
        symbols=["BTC/USD", "SOL/USD"]
    )

    # 2. Inspect Quarantined Anomaly Records
    quarantine = admin.query_quarantine(limit=50)
    print(f"Quarantined Anomalies: {len(quarantine)}")
    for record in quarantine:
        print(f"  Anomaly: {record.get('reason')} on {record.get('instrument_id')}")

    # 3. Export Standalone Cryptographic Audit Proof Bundle
    proof = admin.export_audit()
    with open("compliance_audit_proof.json", "w") as f:
        import json
        json.dump(proof, f, indent=2)
    print("Exported audit proof bundle successfully.")
```

---

## 4. Ultra-Low-Latency Local Transports (SHM & Binary Wire)

When running on the same host as the MDRAP engine, `MDRAPClient` can bypass network sockets entirely:

```python
# Connect to POSIX Shared Memory ring buffer for <1µs tick ingress
client = MDRAPClient(
    transport="shm",
    shm_name="mdrap_feed"
)

# Connect to ultra-low-latency binary wire protocol
client = MDRAPClient(
    host="127.0.0.1",
    port=9001,
    transport="binary",
    auth_token="mdrap_live_..."
)
```
