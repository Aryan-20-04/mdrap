# MDRAP Quant-Ready Python SDK Guide

Welcome to the MDRAP SDK! This guide explains how to connect to the TCP Gateway to ingest real-time tick data, build Pandas DataFrames, and execute algorithmic paper trades.

## 1. Connecting to the Live Stream
The `MDrapClient` connects to the gateway via raw TCP sockets, providing microsecond latency.

```python
import asyncio
from mdrap.sdk.client import MDrapClient

async def main():
    client = MDrapClient(host="127.0.0.1", port=9000)

    def on_event(msg):
        print(f"Received tick: {msg}")

    client.on("event", on_event)
    await client.subscribe()

asyncio.run(main())
```

## 2. Advanced: Non-Blocking DataFrame Processing
Pandas operations (`pd.concat`) block the python event loop. If you block the loop, the TCP Gateway's backpressure system will explicitly disconnect you to protect server RAM!

To prevent disconnects, use an `asyncio.Queue` and `asyncio.to_thread`:

```python
import asyncio
from mdrap.sdk.client import MDrapClient

async def main():
    client = MDrapClient()
    queue = asyncio.Queue()

    def on_event(msg):
        queue.put_nowait(msg) # Unblocks instantly!

    async def process_queue():
        while True:
            msg = await queue.get()
            # Do heavy pandas work inside asyncio.to_thread...
            
    await asyncio.gather(client.subscribe(), process_queue())
```

## 3. Algorithmic Trading (Paper Sandbox)
MDRAP provides a paper-trading execution engine. Write your logic by inheriting from `Strategy`.

```python
from mdrap.sdk.strategy_sdk import Strategy
from mdrap.sdk.execution import LiveStrategyRunner

class MyAlgo(Strategy):
    def __init__(self):
        super().__init__(name="MyAlgo", symbols=["AAPL"])

    def on_tick(self, event):
        if event.price < 150.0:
            self.buy("AAPL", 100) # Automatically calculates slippage!

runner = LiveStrategyRunner(MyAlgo(), port=9000)
asyncio.run(runner.run())
```

See `src/sdk/strategy_vwap.py` for a complete reference implementation of a VWAP-slicing algorithm.
