# MDRAP

**Market Data Reliability & Acceleration Platform**

MDRAP is an institutional market-data validation, normalization, cross-feed reconciliation, and cryptographic audit sidecar. It sits between upstream raw market feeds (WebSocket, ITCH binary, FIX/FAST, REST) and downstream trading engines, quantitative analytics pipelines, and historical tick databases.

MDRAP converts noisy, delayed, duplicated, and out-of-order market data from multiple feeds into a fast, validated, and canonical stream with mathematical integrity guarantees.

---

## Key Capabilities

- **Lossless IngestLog Durability**: WAL-first append with segment CRC32C checksums, atomic directory synchronization, and deterministic recovery guaranteeing 100% acknowledged event safety across abrupt crashes.
- **Deterministic Quality Engine**: 24 financial quality checks, Welford numerical variance filtering, dynamic volatility bands, bounded-memory deduplication, and non-downgradable quality states (`INVALID > SUSPICIOUS > VALID`).
- **Never Silently Discard Bad Data**: Invalid market events are quarantined with full cryptographic lineage and cause codes; drop and eviction counters are continuously exported.
- **Dual-Tier Acceleration & Fallback Parity**: Optional native C hot path (`src/fastpath.c`, `src/mdrap_core.c`) with AVX2 SIMD acceleration and lock-free SPSC shared memory ring buffer (`shm.py`), backed by 100% numerically identical pure-Python fallback.
- **Distributed Safety & Fast Failover**: Quorum-based lease coordination and epoch-fenced storage boundaries preventing split-brain writes, with median failover lifecycle of 105.01 ms (p50 across 100 trials).
- **High-Throughput Network Fanout**: Non-blocking asynchronous TCP fanout engine delivering 32,000+ frames/sec to downstream subscriber desks.

---

## Installation & Packaging Reality

> [!IMPORTANT]
> **MDRAP is not currently published on the public PyPI repository.**
> Installation is supported directly from local Git checkouts or private wheel builds.

Python **3.10** or higher is required.

```bash
# Clone the repository
git clone https://github.com/mdrap-org/mdrap.git
cd mdrap

# Core engine installation (stdlib-only, zero mandatory external dependencies)
python -m pip install -e .

# Full installation with REST/WebSocket API and Rich terminal UI
python -m pip install -e ".[api,ui]"
```

### Companion Packages

Non-core analytical and domain-specific subsystems are segregated into companion distributions in `packages/`:

```bash
# Options pricing, binomial trees, implied volatility & Greeks
python -m pip install -e packages/mdrap-options

# Transaction Cost Analysis (TCA) & execution benchmarks
python -m pip install -e packages/mdrap-analytics

# Algorithmic execution strategies & risk management
python -m pip install -e packages/mdrap-strategies

# AIS vessel tracking & maritime intelligence
python -m pip install -e packages/mdrap-contrib-vessel
```

All companion packages can be imported directly or accessed through backward-compatible shims in `mdrap.*` (e.g. `from mdrap.options import OptionsChain`).

See the [Installation Guide](docs/installation.md) for full details on native C compilation (`python build_fastpath.py`) and Docker deployment.

---

## Verified Quickstart

### 1. Embedded Canonical Engine API

The high-level `Engine` provides synchronous WAL durability, data normalization, quality evaluation, and projection queries:

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

### 2. Running the API Server

MDRAP includes an asynchronous REST and WebSocket streaming server:

```bash
# Set mandatory cryptographic salt and initial admin token
export MDRAP_API_KEY_SALT="your-cryptographic-salt-min-16-chars"
export MDRAP_INITIAL_ADMIN_KEY="your-admin-bootstrap-token"

# Launch production server
mdrap serve --host 127.0.0.1 --port 8000 --db data/mdrap.db
```

Verify health and readiness:

```bash
curl -s http://127.0.0.1:8000/v1/health
curl -s http://127.0.0.1:8000/v1/readiness
```

See [docs/quickstart.md](docs/quickstart.md) and [docs/api.md](docs/api.md) for verified `curl` ingest and query examples.

---

## Operational Status & Verification

MDRAP adheres to Design Principle 2: **Measure before claiming**.

| Milestone | Scope | Verified Result | Status |
| --- | --- | --- | --- |
| **Mode A** | Single-Node Process & IPC | 1,240 automated test suite passed cleanly | **VERIFIED** |
| **Mode B** | Independent-Host Staging | Failover p50: 105.01 ms (100 trials, 0% data loss) | **VERIFIED** |
| **Mode C** | Live Venue Production Feeds | Direct exchange connectivity & licensing | **GATED** |
| **Mode D** | Physical Kernel-Bypass NIC/FPGA | Dedicated bare-metal testbench validation | **GATED** |

### Verified Boundaries & Open Constraints

- **Adapter Supervision**: There is no supervisor that owns adapter lifecycle or connects adapters to the HTTP server. Feed registration (`POST /v1/feeds`) stores metadata as `REGISTERED_NOT_RUNNING`. Ingestion is driven by `POST /v1/ingest`, the gateway daemon, or direct Python scripts.
- **WebSocket Streaming**: `/v1/events/stream` broadcasts events ingested via `POST /v1/ingest`; it does not automatically connect to external exchange feeds.
- **Mandatory Salt**: Startup outside demo mode requires setting `MDRAP_API_KEY_SALT`. See [`.env.example`](.env.example).
- **Public Packaging**: Not published on public PyPI; distribute via source checkouts or private wheels.

For full technical specifications, consult [docs/status.md](docs/status.md), [docs/verified-gap-report.md](docs/verified-gap-report.md), and [docs/architecture.md](docs/architecture.md).

---

## Documentation Index

- **[Audience Index](docs/README.md)**: Documentation roadmaps for Quants, Platform Engineers, Developers, and Auditors.
- **[Installation Guide](docs/installation.md)**: Source setup, optional extras, native C hot path, and Docker.
- **[Configuration Guide](docs/configuration.md)**: Complete environment variable reference and `mdrap.toml` layering.
- **[CLI Reference Manual](docs/cli-reference.md)**: All 69 commands and aliases generated from live `argparse`.
- **[REST & WebSocket API](docs/api.md)**: Complete route table with verified `curl` examples.
- **[Data Quality Rules](docs/quality-rules.md)**: 24 financial quality checks, bitmasks, and anomaly thresholds.
- **[Cryptographic Audit Trail](docs/audit-log-format.md)**: SHA-256 Merkle chains and HMAC checkpoints.
- **[Security Policy](SECURITY.md)**: Vulnerability disclosure and supported versions.

---

## License

MIT. See [LICENSE](LICENSE).
