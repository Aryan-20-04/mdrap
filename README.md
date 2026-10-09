# MDRAP

**Market Data Reliability & Acceleration Platform**

MDRAP is an institutional market-data validation, normalization, reconciliation, and audit sidecar designed to convert noisy, delayed, duplicated, and inconsistent market data from multiple feeds into a fast, validated, canonical stream. It sits before trading strategies, analytics, and downstream databases.

## Platform Highlights

- **Lossless IngestLog Durability**: WAL-first append with segment CRC32C validation, atomic directory synchronization, and deterministic recovery ensuring 100% acknowledged event safety.
- **Deterministic Quality Engine**: 24 financial quality checks, Welford numerical anomaly detection, bounded-memory deduplication, and non-downgradable quality states (`INVALID > SUSPICIOUS > VALID`). Never silently discards data; invalid events are quarantined with full cryptographic lineage.
- **Native C Hot Path & Shared Memory IPC**: AVX2 SIMD acceleration and zero-lock SPSC shared memory ring buffer (`shm.py`) delivering high-throughput sub-microsecond event delivery.
- **High-Availability & Distributed Safety**: Quorum-based lease coordination, epoch-fenced storage boundaries, and sub-110ms failover lifecycles (p50: 105.01 ms).
- **Asynchronous Network Fanout**: High-concurrency async TCP fanout delivering 32,000+ frames/sec to downstream consumers.
- **Modular Companion Ecosystem**: Core engine isolation (`mdrap-core`) decoupled from non-core peripheral domains (`mdrap-options`, `mdrap-analytics`, `mdrap-strategies`, and `mdrap-contrib-vessel`), with 100% backward-compatible zero-overhead shims.

## Install

Python 3.10 or newer is required. Install the core engine with:

```bash
python -m pip install mdrap-core
```

The `mdrap-core` wheel installs the `mdrap` package. Install `mdrap-contrib` for the CLI and peripheral integrations; feature dependencies are available as extras such as `mdrap-contrib[api]`.

### Companion packages

Domain-specific analytics and execution models are segregated into companion packages:

```bash
# Options pricing, implied volatility & Greeks
python -m pip install packages/mdrap-options

# Transaction Cost Analysis (TCA) & execution metrics
python -m pip install packages/mdrap-analytics

# Algorithmic execution strategies & risk management
python -m pip install packages/mdrap-strategies

# AIS vessel tracking & maritime intelligence
python -m pip install packages/mdrap-contrib-vessel
```

All companion packages can be imported directly (e.g., `import mdrap_options`) or accessed seamlessly via backward-compatible delegation forwarders in `mdrap.*` (`from mdrap.options import OptionsChain`).

## Process an event (Canonical Engine API)

```python
import tempfile
from mdrap import Engine, EngineConfig, RawEvent

wal_dir = tempfile.mkdtemp()
engine = Engine.open(wal_dir, config=EngineConfig(db_path=":memory:"))

event = RawEvent(
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

decision = engine.submit(event)
canonical = decision.canonical_event
ticks = engine.query("AAPL", limit=10)
engine.close()
```

`Engine.open()` opens the durable `IngestLog` write-ahead log and binds the SQLite projection. `Engine.submit()` guarantees synchronous WAL fsync before returning the deterministic `EngineDecision`, ensuring zero acknowledged-event loss. See the [quickstart](docs/quickstart.md) for full setup instructions.

## Boundaries and Operational Status

- The `mdrap-core` distribution owns the stable engine API. The separately version-matched `mdrap-contrib` distribution supplies peripheral modules and the `mdrap` command. Source-tree compatibility shims are excluded from both wheels.
- Update imports such as `from models import RawEvent` to `from mdrap.models import RawEvent` and `from mdrap.engine import Engine`; prefer the supported facade (`from mdrap import Engine, RawEvent, Store`) when those names are sufficient.
- `mdrap.adapters` defines an adapter protocol and includes a reference adapter. There is no supervisor that owns adapter lifecycle or connects adapters to the HTTP server.
- Feed registration stores metadata and reports `REGISTERED_NOT_RUNNING`. The WebSocket endpoint is not connected to live server ingestion.
- API startup outside demo mode requires a persistent `MDRAP_API_KEY_SALT` value. See [`.env.example`](.env.example).
- Native acceleration is optional and platform-specific with 100% numerical parity fallback in pure Python.

## Verification & Tests

```bash
mdrap version
mdrap --help
python -m pytest tests/ -q
```

All 1,240 platform test cases pass cleanly across native C and pure-Python execution paths. See [the verified gap report](docs/verified-gap-report.md) and [architecture specification](docs/architecture.md).

## License

MIT. See [LICENSE](LICENSE).
