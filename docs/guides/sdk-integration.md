# SDK Integration Guide

## Goal
Embed MDRAP directly into a proprietary trading application, execution container, or quantitative pipeline using the high-performance in-process `Engine` API.

## Prerequisites
- Python 3.10+
- `mdrap-core` installed (`pip install -e .`)
- Zero external services or databases required (uses embedded SQLite and filesystem WAL)

## Steps

### Step 1: Instantiate the Engine
Use `Engine.open()` to initialize the synchronous write-ahead log (`IngestLog`) and bind the SQLite projection database:

<!-- doc-test: python -->
```python
# doc-test
import tempfile, time
from mdrap import Engine, EngineConfig, RawEvent, QualityStatus

wal_dir = tempfile.mkdtemp()
config = EngineConfig(
    db_path=":memory:",
    fsync_policy="grouped_by_time",  # High-throughput asynchronous fsync
    max_segment_bytes=64 * 1024 * 1024,
)
engine = Engine.open(wal_dir, config=config)

# Construct a normalized raw event from your market feed adapter
now = time.time()
trade = RawEvent(
    source="NYSE",
    payload={
        "instrument": "MSFT",
        "event_type": "TRADE",
        "price": 420.50,
        "quantity": 100.0,
        "exchange_ts": now,
        "sequence": 101,
    },
    receive_timestamp=now,
)

# Submit to the engine pipeline (WAL append -> Normalization -> Quality rules)
decision = engine.submit(trade)
event = decision.canonical_event

print(f"Decision Status: {event.quality_status.name}")
print(f"Validated Price: {event.price} (Qty: {event.quantity})")

# Query recent canonical state
recent = engine.query("MSFT", limit=10)
print(f"Historical ticks stored: {len(recent)}")

engine.close()
```

### Step 2: Handle Quality Anomalies & Quarantine
If an incoming event violates market structure rules (e.g. crossed book or price band limit breach), MDRAP flags the event without throwing an uncaught exception:

```python
# Intentionally inject an invalid crossed quote
crossed_quote = RawEvent(
    source="NASDAQ",
    payload={
        "instrument": "MSFT",
        "event_type": "QUOTE",
        "bid": 425.00,  # Bid higher than ask
        "ask": 420.00,
        "exchange_ts": now,
    },
    receive_timestamp=now,
)

dec = engine.submit(crossed_quote)
if dec.canonical_event.quality_status == QualityStatus.INVALID:
    print(f"Quarantined event: {dec.canonical_event.reasons}")
```

## Expected Output
```text
Decision Status: VALID
Validated Price: 420.5 (Qty: 100.0)
Historical ticks stored: 1
```

## Troubleshooting
- **`SchemaError` during normalization**: Ensure your feed payload includes required fields (`instrument`, `event_type`, `price`/`quantity` for trades, or `bid`/`ask` for quotes).
- **WAL disk space exhaustion**: In production, configure automatic segment rotation and archive old WAL segments using `mdrap.archive` or background retention scripts.

## Next Steps
- Inspect the full event model in the [Event Schema Reference](../reference/event-schema.md).
- Learn how to verify tamper-evident audit logs in the [Audit Verification Guide](audit-verification.md).
