# CLI Reference Manual

MDRAP provides a unified command-line interface (`mdrap`) for running the server, interacting with live and historical data, administering keys, executing chaos drills, and verifying audit logs.

```bash
mdrap [COMMAND] [ARGS...] [FLAGS]
```

## Global Options

- `--json`: Format all output as machine-readable JSON.
- `--no-color` / `--plain`: Suppress ANSI color codes (honors `NO_COLOR=1` environment variable).
- `-v`, `--verbose`: Enable verbose debug logging to stderr.

---

## Core Server & Daemon Commands

### `mdrap serve`
Starts the asynchronous FastAPI REST and WebSocket streaming server.

```bash
mdrap serve [--host HOST] [--port PORT] [--db PATH] [--workers N]
```

- `--host`: Bind address (default: `127.0.0.1`).
- `--port`: HTTP port (default: `8000`).
- `--db`: SQLite projection database path (default: `data/mdrap.db`).
- `--workers`: Number of Uvicorn worker processes (default: `1`).

### `mdrap daemon`
Starts the low-latency TCP push daemon (`MarketDataDaemon`).

```bash
mdrap daemon [--host HOST] [--port PORT] [--db PATH]
```

- `--host`: Listen host (default: `127.0.0.1`).
- `--port`: Listen TCP port (default: `9988`).

### `mdrap gateway`
Runs the raw TCP socket gateway for ingesting line-delimited or binary feed packets.

```bash
mdrap gateway [--port PORT] [--wal PATH]
```

---

## Operations & Diagnostics

### `mdrap doctor`
Performs comprehensive system diagnostics, reporting CPU architecture, AVX2 SIMD acceleration, native C compiler availability, storage I/O throughput, and installed companion packages.

```bash
mdrap doctor [--json]
```

### `mdrap status`
Displays active service health, connected venues, event throughput counters, and ring buffer utilization.

```bash
mdrap status [--db PATH]
```

---

## Audit & Security Commands

### `mdrap audit`
Cryptographically verifies the rolling SHA-256 Merkle chain or exports a signed audit package.

```bash
mdrap audit verify [--db PATH]
mdrap audit export [--db PATH] [--out ARCHIVE_ZIP]
```

### `mdrap keys`
Manages API client entitlements and salted HMAC token hashes.

```bash
# Register a new API key with a designated role
mdrap keys create --client-id "quant_desk_1" --role "OPERATOR"

# List active API keys (tokens are redacted)
mdrap keys list

# Revoke an active key by ID or token
mdrap keys revoke --key-id <KEY_ID>
```

---

## Market Data Inspection & Analytics

### `mdrap bbo [SYMBOL]`
Queries and displays the real-time or historical Best Bid and Offer (BBO) for a symbol across normalized venues.

```bash
mdrap bbo AAPL
```

### `mdrap depth [SYMBOL]`
Renders a live terminal depth-of-book (Level 2) visualization.

```bash
mdrap depth BTC-USDT --levels 10
```

### `mdrap vwap [SYMBOL]`
Calculates volume-weighted average price (VWAP) curves and market impact shortfall estimates.

```bash
mdrap vwap AAPL --volume 5000
```

### `mdrap tca [SYMBOL]`
Generates Transaction Cost Analysis (TCA) and execution benchmark reports (SEC 606 compatible).

```bash
mdrap tca MSFT --benchmark arrival
```

---

## Simulation & Benchmarking

### `mdrap simulate`
Runs the deterministic market data feed simulator with fault injection.

```bash
mdrap simulate --events 1000 --seed 42 --anomaly-rate 0.05
```

### `mdrap benchmark`
Executes reproducible throughput and latency benchmarks.

```bash
mdrap benchmark --iterations 100000 --json
```
