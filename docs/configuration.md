# MDRAP Configuration Guide

This guide describes the platform configuration model, hierarchical configuration layering via `mdrap.toml`, and the complete catalog of supported environment variables.

---

## 1. Configuration Precedence Ladder

MDRAP enforces a strict four-tier configuration precedence ladder. Settings defined at higher tiers override those defined at lower tiers:

```
[ Tier 1 ] Explicit CLI Flags (--host, --port, --db)
    │
    ▼
[ Tier 2 ] Operating System Environment Variables (MDRAP_*)
    │
    ▼
[ Tier 3 ] Hierarchical Configuration File (mdrap.toml)
    │
    ▼
[ Tier 4 ] Hardcoded Engine Defaults (Spec §26)
```

If a setting is specified in both `mdrap.toml` and an environment variable, the environment variable takes precedence. If a command-line flag is passed to the CLI, it overrides both.

---

## 2. Hierarchical `mdrap.toml` Layering

MDRAP uses `tomllib` (Python standard library) to load `mdrap.toml`. The loader walks upward from the working directory looking for `mdrap.toml`.

### Resolution Precedence Within `mdrap.toml`

Within the TOML file, parameters are resolved down an inheritance ladder:

```
defaults ➔ venue.<VENUE> ➔ venue.<VENUE>.instrument_class.<CLASS> ➔ venue.<VENUE>.instrument.<SYMBOL>
```

This design allows global risk thresholds (e.g., staleness threshold of 50ms) to be tightened or loosened per venue (e.g., crypto venues vs equity exchanges) or per specific liquid instrument (e.g., `BTCUSDT`).

### Example `mdrap.toml`

```toml
# Top-level sections allowed: defaults, venue, system, storage, network, security

[defaults]
staleness_threshold_s = 0.05       # 50 ms default staleness threshold
price_anomaly_stddev = 6.0         # 6-sigma outlier rejection
price_window = 50                  # Welford sliding window length
dedup_cache_size = 200000          # Bounded memory LRU deduplication slots
circuit_filter_pct = 0.10          # 10% maximum price step change

[storage]
wal_path = "data/wal"
db_path = "data/mdrap.db"
max_segment_bytes = 67108864       # 64 MB WAL segment size
sync_mode = "NORMAL"

[network]
host = "0.0.0.0"
port = 8000
listen_backlog = 1024
max_clients = 256
enable_shm = true
shm_name = "mdrap_feed"

# Venue-specific overrides:
[venue.binance]
staleness_threshold_s = 2.0        # Relaxed staleness for public internet crypto WebSocket
price_anomaly_stddev = 5.0

[venue.binance.instrument_class.crypto]
staleness_threshold_s = 1.5

[venue.binance.instrument.BTCUSDT]
staleness_threshold_s = 0.5        # Tighter threshold for highly liquid BTCUSDT
price_anomaly_stddev = 4.0

[venue.XNSE]
staleness_threshold_s = 0.10
circuit_filter_pct = 0.10

[venue.XETR]
staleness_threshold_s = 0.05
circuit_filter_pct = 0.05
```

### Deterministic Configuration Hashing

Whenever MDRAP starts or records an audit event, it generates a canonical SHA-256 fingerprint of the active configuration via `compute_config_hash()`. Any runtime divergence or uncommitted configuration drift is captured in cryptographic lineage logs.

---

## 3. Environment Variable Catalog

Below is the complete reference of environment variables supported across MDRAP:

### Server, Networking & API

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_HOST` | string | `0.0.0.0` | Listening IP address for the HTTP/WebSocket server (`mdrap serve`). |
| `MDRAP_PORT` | int | `8000` | Listening TCP port for the HTTP/WebSocket server. |
| `MDRAP_CORS_ORIGINS` | string | `*` | Comma-separated list of allowed CORS origins for browser clients. |
| `MDRAP_TRUSTED_PROXY_IPS` | string | `""` | Comma-separated IP addresses of trusted reverse proxies. `X-Forwarded-For` is ignored unless requests arrive from an explicit trusted proxy. |
| `MDRAP_METRICS_AUTH` | int (bool) | `1` | When `1`, `/metrics` requires an active API key for non-loopback clients. |
| `MDRAP_API_STALENESS_S` | float | `2.0` | Maximum age in seconds before cached quotes or prices report as stale. |
| `MDRAP_ALLOW_INTERNAL_WEBHOOKS` | int (bool) | `0` | Enable loopback webhooks (disabled by default to prevent SSRF). |

### Security, Authentication & RBAC

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_API_KEY_SALT` | string | *(none)* | **Required outside demo mode.** Cryptographic salt used for key derivation and SHA-256 hashing. Platform fails closed if empty. |
| `MDRAP_INITIAL_ADMIN_KEY` | string | *(none)* | Optional static token to provision as master `ADMIN` key on clean database startup. |
| `MDRAP_AUTO_BOOTSTRAP_ADMIN` | int (bool) | `1` | Automatically generate and log a master admin key on startup if no keys exist. |
| `MDRAP_API_KEY_<ROLE>` | string | *(none)* | Register a static environment API key for a given role (e.g., `MDRAP_API_KEY_ADMIN=token`, `MDRAP_API_KEY_VIEWER=token`). |
| `MDRAP_REQUIRE_AUTH` | string (bool) | `true` | Enforce API key authentication across all operational endpoints. |
| `MDRAP_DEMO` | int (bool) | `0` | When `1`, activates built-in demo keys and bypasses mandatory salt requirement for sandboxes. |
| `MDRAP_REQUIRE_TLS` | int (bool) | `0` | Reject non-TLS plaintext connections. |
| `MDRAP_AUDIT_KEY` | string | `""` | HMAC-SHA256 secret key used for signing cryptographic audit log checkpoints. |

### Storage & Write-Ahead Log (WAL)

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_DB_PATH` | string | `data/mdrap.db` | Primary SQLite database path for canonical tables and event projections. |
| `MDRAP_WAL_PATH` | string | `data/wal` | Write-Ahead Log directory for CRC32C segment durability. |
| `MDRAP_ASYNC_WRITER` | int (bool) | `1` | Enable asynchronous background batch writer for SQLite projections. |
| `MDRAP_DURABILITY_POLICY` | string | `fail_closed` | Durability policy when disk write fails (`fail_closed` or `best_effort`). |
| `MDRAP_DEAD_LETTER_DIR` | string | `data/deadletter` | Directory for quarantined or unpersisted dead-letter transactions. |
| `MDRAP_SQLITE_CACHE_MB` | int | `64` | SQLite in-memory page cache size in megabytes. |
| `MDRAP_SQLITE_MMAP_MB` | int | `256` | SQLite memory-mapped I/O size in megabytes. |

### Native C Kernel & Acceleration

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_DISABLE_FASTPATH` | int (bool) | `0` | When `1`, disables compiled C extension and enforces pure Python fallback. |
| `MDRAP_AUTO_COMPILE` | int (bool) | `0` | When `1`, attempts automatic background C compilation on import if missing. |
| `MDRAP_TIMING_SAMPLE_MASK` | int | `""` | Bitmask for high-frequency latency measurement sampling (e.g. `0x3F` for 1-in-64). |

### Quality Engine & Out-of-Order Handling

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_REORDER_WINDOW_S` | float | `0.0` | Maximum time buffer (seconds) to hold incoming events for resequencing. |
| `MDRAP_REORDER_SLOTS` | int | `32` | Maximum slots allocated for reordering buffer per symbol. |

### Shared Memory (IPC)

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MDRAP_SHM_WATERMARK_PCT` | float | `0.80` | High-watermark occupancy threshold triggering backpressure warning. |
| `MDRAP_SHM_WRITER_TIMEOUT` | float | `4.0` | Timeout in seconds before writer aborts an unconsumed ring buffer slot. |

### Vendor Market Data API Credentials

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `POLYGON_API_KEY` | string | `""` | API key for Polygon.io direct market data feed adapter. |
| `DATABENTO_API_KEY` | string | `""` | API key for Databento historical and live feeds. |
| `CRYPTOHFTDATA_API_KEY` | string | `""` | API key for institutional CryptoHFTData feed. |

### Terminal & Environment

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `NO_COLOR` / `MDRAP_NO_COLOR` | int (bool) | *(none)* | When set, disables all ANSI colors and terminal styling. |
