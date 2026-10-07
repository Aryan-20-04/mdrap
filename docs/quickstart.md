# MDRAP Quickstart

This guide runs MDRAP’s current in-process pipeline and optional API. It does not configure a live feed: server feed registration is metadata only, and the server WebSocket is not connected to a feed runtime.

## Requirements and installation

- Python 3.10 or newer
- The `api` extra is needed only to run the HTTP service

Install the core package and contrib API extra:

```bash
python -m pip install mdrap-core "mdrap-contrib[api]"
```

For non-demo API startup, configure a stable, private API-key salt before starting the service:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Store the generated value in `MDRAP_API_KEY_SALT` using the environment or a protected secrets store. Keep it unchanged across restarts; changing it makes existing API key hashes unusable.

## Process one event (Canonical Engine API)

```python
import tempfile
from mdrap import Engine, EngineConfig, RawEvent

wal_dir = tempfile.mkdtemp()
engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

raw = RawEvent(
    source="EXAMPLE",
    payload={
        "instrument": "AAPL",
        "event_type": "TRADE",
        "exchange_ts": 1_800_000_000.0,
        "sequence": 1,
        "price": 200.0,
        "quantity": 10.0,
    },
)

decision = engine.submit(raw)
canonical = decision.canonical_event
ticks = engine.query("AAPL", limit=10)
engine.close()
```

`Engine.open()` opens the durable `IngestLog` write-ahead log and binds the SQLite projection. `Engine.submit()` guarantees synchronous WAL fsync before returning the deterministic `EngineDecision`, ensuring zero acknowledged-event loss.

### Legacy In-Process Pipeline

```python
from mdrap import Pipeline, RawEvent, Store

store = Store(":memory:")
pipeline = Pipeline(store=store, async_writer=False, flush_interval_s=0)
raw = RawEvent(
    source="EXAMPLE",
    payload={
        "instrument": "AAPL",
        "event_type": "TRADE",
        "exchange_ts": 1_800_000_000.0,
        "sequence": 1,
        "price": 200.0,
        "quantity": 10.0,
    },
)
event = pipeline.process_one(raw)
pipeline.finish()
store.close()
```

## Start the optional API

```bash
mdrap serve --host 127.0.0.1 --port 8000 --db ./mdrap.db
```

The API health route is `http://127.0.0.1:8000/v1/health`. To inspect CLI commands:

```bash
mdrap --help
mdrap version
```

Feed registration does not start an adapter. The API has no server-managed source lifecycle or live adapter-to-WebSocket event path yet.

## Test the checkout

```bash
python -m pytest
```

The repository’s default pytest configuration excludes tests marked `slow` and `network`. Those tests need separate execution and environment evidence.
