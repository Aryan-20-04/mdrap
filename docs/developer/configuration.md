# Configuration Reference

MDRAP configuration is structured in standard TOML format. It can be supplied via the `--config <path>` flag or the `MDRAP_CONFIG` environment variable.

---

## 1. Complete Configuration Template

```toml
[general]
instance_id = "prod-ny4-sidecar-01"  # Unique identifier for this monitoring node
data_dir = "/data"                  # Base directory for database and evidence exports
log_level = "INFO"                  # DEBUG, INFO, WARNING, ERROR, CRITICAL
data_mode = "LIVE"                  # LIVE, REPLAY, or SIMULATION

[storage]
database_path = "/data/mdrap.db"     # SQLite database path
journal_mode = "WAL"                # WAL (recommended) or DELETE
synchronous = "NORMAL"              # OFF, NORMAL (recommended for WAL), FULL
busy_timeout_ms = 5000              # Milliseconds to wait before SQLite busy error
batch_size = 1000                   # Events to buffer before disk commit
wal_autocheckpoint_pages = 1000     # WAL auto-checkpoint threshold (pages)
retention_days = 30                 # Days before automatic cleanup of archived events

[api]
enabled = true                      # Expose local read-only REST and WebSocket API
host = "127.0.0.1"                  # Must bind to 127.0.0.1 for local sidecar isolation
port = 8000                         # Port for API server
cors_origins = []                   # Allowed CORS origins (empty array disables CORS)
rate_limit_per_second = 100         # Max requests per second per IP
enable_websockets = true            # Expose live canonical WebSocket stream

[security]
read_only_enforced = true           # Strictly forbids mutating endpoints or execution code
require_salt = true                 # Enforce cryptographic salt for API hashing
salt = "CHANGE_ME_TO_A_SECURE_RANDOM_HEX_SALT" # 32-byte hex salt
mtls_enabled = false                # Require mutual TLS for API connections

[quality]
max_clock_drift_ms = 50.0           # Max allowed difference between exchange and receive ts
max_spread_bps = 500.0              # Max allowed spread in basis points before anomaly
stale_threshold_seconds = 3.0       # Max quiet interval before tick is flagged STALE
price_jump_threshold_ratio = 0.10   # Max percentage price jump relative to moving window
min_book_depth_levels = 5           # Minimum L2 depth levels required
enforce_sbe_bounds = true           # Validate integer and decimal bounds on SBE payloads

[providers.coinbase]
enabled = true                      # Activate Coinbase connector
ws_url = "wss://ws-feed.exchange.coinbase.com"
channels = ["ticker", "level2"]
instruments = ["BTC-USD", "ETH-USD"]
read_only = true

[providers.kraken]
enabled = true                      # Activate Kraken connector
ws_url = "wss://ws.kraken.com"
channels = ["ticker", "book-10"]
instruments = ["XBT/USD", "ETH/USD"]
read_only = true

[providers.binance]
enabled = false                     # Activate Binance connector
ws_url = "wss://stream.binance.com:9443/ws"
channels = ["bookTicker", "depth5@100ms"]
instruments = ["btcusdt", "ethusdt"]
read_only = true

[incidents]
auto_quarantine = true              # Automatically isolate anomalous ticks from output
export_directory = "/data/incidents"# Location to write forensic evidence packages
merkle_batch_size = 500             # Event count per Merkle tree anchor batch
notification_webhook = ""           # Optional HTTPS webhook for incident alerting
```

---

## 2. Environment Variables

All configuration options can be overridden or supplemented using environment variables:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `MDRAP_CONFIG` | String | `None` | Path to active TOML configuration file |
| `MDRAP_DATA_DIR` | String | `data` | Working directory for storage & logs |
| `MDRAP_API_PORT` | Integer | `8000` | Port for read-only REST API |
| `MDRAP_DISABLE_FASTPATH`| Boolean | `0` | Set to `1` to force pure-Python fallback execution |
| `MDRAP_SALT` | String | `None` | Cryptographic salt override (32-byte hex string) |
| `MDRAP_LOG_LEVEL` | String | `INFO` | Console logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 3. Security Invariants

- **Loopback Default**: `api.host` defaults to `127.0.0.1`. Never expose MDRAP directly to untrusted public networks without an authenticating reverse proxy (e.g., Envoy or NGINX with mTLS).
- **Salt Requirement**: In production, if `require_salt = true`, MDRAP will refuse to start if the salt is empty, default, or fewer than 16 hex characters.
- **Read-Only Mode**: `security.read_only_enforced` cannot be toggled to false via config or flags. It is hardcoded as an architectural invariant.
