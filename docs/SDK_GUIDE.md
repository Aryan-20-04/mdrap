# MDRAP Quant-Ready Python SDK Guide

Welcome to the MDRAP SDK! This guide explains how to connect downstream applications, sidecars, and quant research pipelines to MDRAP's read-only market data infrastructure using either TCP streaming or sub-microsecond shared memory IPC.

> [!IMPORTANT]
> **Governance Notice: NO DEPLOYMENT**
> MDRAP is strictly a read-only market data validation and normalization infrastructure sidecar. It contains zero order routing, strategy activation, or execution capabilities.

---

## 1. Connecting via TCP StreamClient

The `StreamClient` connects to the running MDRAP streaming daemon (`mdrap daemon`) over a local TCP socket for streaming canonical market events:

```python
import asyncio
from mdrap.service import StreamClient

async def main():
    client = StreamClient(host="127.0.0.1", port=9000)
    await client.connect()

    async for event in client.stream_events(symbol="BTCUSDT"):
        print(f"[{event['timestamp']}] {event['symbol']} {event['event_type']} @ {event['price']}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 2. Ultra-Low Latency IPC via SHMReader

For sub-microsecond consumption without TCP overhead, downstream sidecars consume canonical events directly from POSIX/Windows shared memory via `SHMReader`:

```python
from mdrap.shm import SHMReader

def main():
    reader = SHMReader(ring_name="mdrap_canonical_ring")
    reader.attach()

    try:
        while True:
            batch = reader.read_batch(max_events=128)
            for event in batch:
                print(f"Seq: {event.sequence} | {event.symbol} {event.event_type} @ {event.price}")
    finally:
        reader.close()

if __name__ == "__main__":
    main()
```

---

## 3. Asynchronous Downstream Processing

To prevent blocking the event stream with heavy downstream computation (e.g., pandas calculations), use an `asyncio.Queue` and thread pool:

```python
import asyncio
from mdrap.service import StreamClient

async def main():
    client = StreamClient(host="127.0.0.1", port=9000)
    await client.connect()
    queue = asyncio.Queue(maxsize=10000)

    async def ingest_worker():
        async for event in client.stream_events():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Record dropped frame / backpressure counter

    async def analytics_worker():
        while True:
            event = await queue.get()
            # Offload heavy analytics to thread pool
            await asyncio.to_thread(process_event, event)
            queue.task_done()

    await asyncio.gather(ingest_worker(), analytics_worker())

def process_event(event):
    # Quant analytics, feature engineering, or external database write
    pass

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Querying Historical Canonical Data

Downstream research platforms query sanitized canonical tick history using `Engine.open()`:

```python
from mdrap import Engine, EngineConfig

engine = Engine.open(
    wal_dir="./data/wal",
    config=EngineConfig(db_path="./data/mdrap.db")
)

# Fetch latest validated ticks for quantitative analysis
ticks = engine.query("BTCUSDT", limit=1000)
print(f"Retrieved {len(ticks)} validated canonical ticks")
engine.close()
```
