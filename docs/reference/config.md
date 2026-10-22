# Configuration Reference Manual

MDRAP supports hierarchical, layered configuration through TOML files (`mdrap.toml`) and environment variables.

---

## 1. Resolution Hierarchy

Configuration values resolve using strict hierarchical inheritance:

```text
Defaults  ──>  Venue  ──>  Instrument Class  ──>  Specific Instrument
```

More specific scopes override broader defaults. For example, setting `staleness_threshold_s = 0.5` on `venue.binance.instrument.BTCUSDT` overrides the venue default (`2.0`) and global default (`0.05`).

---

## 2. Configuration Parameters

```toml
[defaults]
staleness_threshold_s = 0.05       # Max age before an event is marked STALE (seconds)
price_anomaly_stddev = 6.0         # Welford variance threshold factor (k * sigma)
price_window = 50                  # Rolling window size for statistical anomaly detection
dedup_cache_size = 200000          # Bounded LRU/ring capacity for sequence deduplication
circuit_filter_pct = 0.10          # Daily price band breach limit (0.10 = +/- 10%)
clock_skew_tolerance_s = 5.0       # Max exchange timestamp lead ahead of local gateway time
```

### Venue-Specific Scopes

```toml
[venue.binance]
staleness_threshold_s = 2.0
price_anomaly_stddev = 5.0

[venue.binance.instrument_class.crypto]
staleness_threshold_s = 1.5

[venue.binance.instrument.BTCUSDT]
staleness_threshold_s = 0.5
price_anomaly_stddev = 4.0

[venue.XNSE]
staleness_threshold_s = 0.10
circuit_filter_pct = 0.10          # NSE/BSE 10% circuit filter

[venue.XETR]
staleness_threshold_s = 0.05
circuit_filter_pct = 0.05          # Deutsche Boerse 5% dynamic price corridor

[venue.XTKS]
staleness_threshold_s = 0.08
circuit_filter_pct = 0.08          # Tokyo Stock Exchange 8% Tokuhai band
```

---

## 3. Environment Variables

| Variable | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `MDRAP_API_KEY_SALT` | String | Yes (API) | Cryptographic salt (minimum 16 characters) used for HMAC token hashing. Startup fails without it. |
| `MDRAP_INITIAL_ADMIN_KEY` | String | No | Bootstrap token granting `ADMIN` role on initial service creation. |
| `MDRAP_CONFIG_FILE` | Path | No | Explicit path to `mdrap.toml` (default: searches working directory and `/etc/mdrap/`). |
| `MDRAP_DISABLE_FASTPATH` | Flag | No | Set to `"1"` to force pure Python execution and bypass native C acceleration. |
| `MDRAP_LOG_LEVEL` | String | No | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`). |
| `MDRAP_DB_PATH` | Path | No | Default SQLite database path (default: `data/mdrap.db`). |
