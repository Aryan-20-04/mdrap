# MDRAP Feed Adapter Developer Guide

This guide describes how to author, test, benchmark, and contribute an institutional exchange or venue feed adapter to MDRAP.

---

## 1. FeedAdapter Protocol Overview

All MDRAP feed adapters implement the runtime-checkable `FeedAdapter` protocol defined in `src/adapters/__init__.py`:

```python
class FeedAdapter(Protocol):
    def connect(self) -> None:
        """Initialize connection, establish TCP/UDP sockets, or open file streams."""
        ...

    def disconnect(self) -> None:
        """Gracefully close sessions and release transport resources."""
        ...

    def receive(self) -> RawEvent | None:
        """Receive the next raw event from the upstream feed buffer."""
        ...

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate upstream venue framing into canonical MDRAP event schema."""
        ...

    def health(self) -> dict:
        """Return connectivity state, packet rates, and stream diagnostics."""
        ...
```

---

## 2. How to Create a Feed Adapter

1. Subclass or implement the protocol methods.
2. In `normalize()`, translate vendor-specific message types into typed `CanonicalEvent` structures (e.g. `EventType.TRADE` or `EventType.QUOTE`).
3. Ensure `health()` exposes connection status, buffer depth, and recent latency.

See the complete reference implementation in `src/adapters/reference.py` (`ReferenceFeedAdapter`).

---

## 3. How to Test a Feed Adapter

Use pytest and deterministic mock events:

```python
def test_custom_feed_adapter():
    adapter = ReferenceFeedAdapter(source_name="MY_VENUE", symbol="AAPL")
    adapter.connect()
    assert adapter.health()["connected"] is True

    adapter.feed_simulated_packet(seq=1, price=150.0, qty=100.0)
    raw = adapter.receive()
    assert raw is not None

    canonical = adapter.normalize(raw)
    assert canonical.instrument_id == "AAPL"
    assert canonical.price == 150.0
    adapter.disconnect()
```

---

## 4. How to Simulate a Feed Adapter

Feed adapters can be driven by the deterministic `MarketSimulator` in `src/simulator.py`:

```python
from simulator import MarketSimulator

sim = MarketSimulator(seed=42)
for raw in sim.generate_events(count=1000):
    # Pass simulated ticks to adapter
    pass
```

---

## 5. How to Benchmark a Feed Adapter

Benchmark normalization throughput and memory allocation with the microbenchmark harness:

```bash
python benchmarks/micro/bench_normalization.py
```

Target: > 500,000 events/second in pure Python, or > 2,000,000 events/second using Native C fastpath.

---

## 6. How to Contribute a Community Feed Adapter

Third-party packages can register their feed adapters dynamically in their `pyproject.toml` using entry points:

```toml
[project.entry-points."mdrap.adapters"]
cme_mdp3 = "mdrap_cme.adapter:CMEMDP3Adapter"
nasdaq_itch = "mdrap_itch.adapter:NasdaqItchAdapter"
```

MDRAP will automatically discover and load community adapters via `adapters.discover_adapters()`.
