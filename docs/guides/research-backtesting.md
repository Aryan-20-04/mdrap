# Research & Backtesting Guide

## Goal
Ingest a deterministic stream of simulated or historical market data, pass it through MDRAP's 15 deterministic quality rules, isolate corrupted and anomalous ticks, and export a clean canonical tick dataset for quantitative backtesting.

## Prerequisites
- Python 3.10+
- `mdrap-core` installed (`pip install -e .`)
- Optional: `[columnar]` extra if exporting to DuckDB / Parquet

## Steps

### Step 1: Initialize the Canonical Engine
Use `Engine.open()` with an in-memory SQLite database or a persistent path:

<!-- doc-test: python -->
```python
# doc-test
import tempfile
from mdrap import Engine, EngineConfig
from mdrap.simulator import FeedSimulator, SimulatorConfig

# Set up an isolated temporary WAL directory
wal_dir = tempfile.mkdtemp()
engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

# Seeded simulator demonstrating fault injection (price anomalies, stale ticks)
sim = FeedSimulator(SimulatorConfig(seed=42, num_events=50, price_anomaly_rate=0.1))

valid_count = 0
flagged_count = 0

for raw, _ in sim.generate():
    decision = engine.submit(raw)
    event = decision.canonical_event
    if event.quality_status.name == "VALID":
        valid_count += 1
    else:
        flagged_count += 1

print(f"Total Processed: {valid_count + flagged_count}")
print(f"Clean Valid Ticks: {valid_count}")
print(f"Anomalous / Quarantined: {flagged_count}")

# Query sanitized canonical ticks for backtesting
ticks = engine.query("AAPL", limit=100)
print(f"Available Ticks for AAPL: {len(ticks)}")
engine.close()
```

### Step 2: Ingest Real Historical Data (CHD Path)
For real-world quantitative backtesting, ingest historical tick archives from the Crypto Historical Data (CHD) client or your internal tick repository:

```python
from mdrap.chd import CHDClient, HistoricalRequest

client = CHDClient(cache_dir="./data/cache")
# Request historical trades partition
request = HistoricalRequest(
    exchange="binance",
    market_type="spot",
    symbol="BTCUSDT",
    data_type="trades",
    date="2026-01-01",
)
partition = client.download(request)
print(f"Downloaded historical partition: {partition.path}")
```

## Expected Output
```text
Total Processed: 50
Clean Valid Ticks: 42
Anomalous / Quarantined: 8
Available Ticks for AAPL: 4
```

## Troubleshooting
- **Ticks flagged with `STALE`**: If historical events use old timestamps and your config enforces maximum staleness, pass `clock_skew_tolerance_s=0` or disable staleness filtering in backtest mode.
- **`PRICE_ANOMALY` false positives**: For high-volatility regimes (e.g. market open or crypto), adjust `price_collar_pct` in `EngineConfig` from default $10\%$ to your strategy's desired bounds.

## Next Steps
- Review all 15 anomaly checks in the [Quality Rules Catalog](../reference/rules.md).
- Learn how to integrate the engine into your trading process in the [SDK Integration Guide](sdk-integration.md).
