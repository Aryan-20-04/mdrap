# MDRAP v5 — Institutional Production Deployment Checklist & Operations Runbook

**Document Classification**: Institutional Operations, Hardening & Production Deployment Runbook  
**Auditor**: Independent Principal Market Data Infrastructure Auditor & Production Reliability Engineer  
**Date**: November 2026  
**Target Platform**: MDRAP (`Market Data Reliability & Acceleration Platform`) v5  

---

## 1. Overview & Release Scope

This checklist defines the mandatory operational gates, security hardening configurations, storage optimizations, and monitoring procedures required before deploying MDRAP into a live production trading infrastructure.

> [!IMPORTANT]
> MDRAP v5 is **CONDITIONALLY PRODUCTION READY**.
> - **Multi-Venue Cryptocurrency Feeds (Coinbase, Binance, Kraken, OKX, Bybit)**: **FULLY CERTIFIED FOR PRODUCTION**.
> - **Commercial Institutional Feeds (Polygon.io Equities, Databento Futures)**: **FAIL-CLOSED BOUNDARY**. Deployment requires valid client-provisioned API keys.

---

## 2. Mandatory Environment Configuration

The following environment variables must be configured in `/etc/mdrap/mdrap.env` (Linux) or Windows System Environment Variables. MDRAP will **refuse to boot** or will fail closed if mandatory security keys are missing.

| Variable Name | Required Value | Default / Fallback | Operational Justification |
|---|---|---|---|
| `MDRAP_ENV` | `production` | `development` | Strict enforcement. Disables unauthenticated debug endpoints, enforces RBAC, enables rate limiters. |
| `MDRAP_API_KEY_SALT` | `[64-character random hex]` | None (*Fails closed*) | Cryptographic salt used for API token PBKDF2 hashing. **Fatal boot error if empty or contains "demo"**. |
| `MDRAP_FSYNC_POLICY` | `grouped_by_time` | `always` | Optimizes WAL persistence. Delivers **26,164 EPS** at **28.3 µs** median latency with bounded 50 ms durability window. |
| `MDRAP_FSYNC_INTERVAL_MS`| `50` | `50` | Background fsync flush interval in milliseconds. |
| `MDRAP_HOST` | `127.0.0.1` | `127.0.0.1` | Bind strictly to loopback or internal private VPC interface. **Never bind to `0.0.0.0` directly**. |
| `MDRAP_PORT` | `8000` | `8000` | REST and WebSocket listener port. Reverse proxy (Caddy/Nginx) handles TLS termination. |
| `MDRAP_CORS_ORIGINS` | `https://trading.firm.internal` | `""` (Empty) | Strict origin whitelist. **Wildcard `*` is rejected in production mode**. |
| `MDRAP_DB_PATH` | `/var/lib/mdrap/data/mdrap.db` | `data/mdrap.db` | Persistent SQLite database file. Must reside on high-speed NVMe storage. |
| `MDRAP_WAL_PATH` | `/var/log/mdrap/wal` | `data/mdrap.db.wal` | Dedicated directory for Write-Ahead Log segments. Must be isolated from temp directories. |
| `MDRAP_QUOTE_TTL_S` | `2.0` | `2.0` | Stale quote TTL eviction threshold. Quotes older than this are evicted from active BBO. |
| `MDRAP_METRICS_AUTH` | `1` | `1` | Enforces API key authentication on Prometheus `/metrics` endpoint when accessed outside loopback. |

---

## 3. Storage & Persistence Hardening

### WAL Configuration
- Ensure the WAL directory resides on a dedicated NVMe partition with direct I/O support.
- File system permissions:
  ```bash
  # Linux
  chmod 0700 /var/log/mdrap/wal
  chown -R mdrap:mdrap /var/log/mdrap/wal
  ```
- Windows: Grant Full Control exclusively to the `MDRAP_SERVICE` account; deny access to `Everyone` and `Users`.
- Maximum segment size: Configure `max_segment_bytes=67108864` (64 MB) for predictable rotation and rapid replay.

### SQLite Database Optimization
- Journal Mode: Enforce `PRAGMA journal_mode=WAL;`
- Synchronous Mode: `PRAGMA synchronous=NORMAL;` (Ensures atomic checkpointing without full synchronous stall)
- Cache Size: `PRAGMA cache_size=-64000;` (Allocates 64 MB page cache)
- Auto-Checkpoint: Run periodic background truncation:
  ```sql
  PRAGMA wal_checkpoint(PASSIVE);
  ```

---

## 4. Shared Memory & Native C Fastpath Deployment

### Native Acceleration Verification
1. Ensure the native C fastpath extension is compiled and available:
   - Linux: `_fastpath_c.so`
   - Windows: `_fastpath_c.cp313-win_amd64.pyd` and `_fastpath_native.dll`
2. Test availability via CLI:
   ```bash
   python -c "from mdrap.fastpath import is_available; print('Native C Acceleration:', is_available())"
   # Output must be: Native C Acceleration: True
   ```
3. Set ring buffer capacity according to expected burst volumes:
   ```bash
   export MDRAP_SHM_SLOT_COUNT=16384
   export MDRAP_SHM_PAYLOAD_SIZE=512
   ```
4. Verify SHM segment permissions (`0600`): Shared memory regions must not be globally readable.

---

## 5. Security & Authentication Runbook

### 1. Generating Production API Keys
Never use static or hardcoded API keys. Generate cryptographically random keys using the CLI or `SecurityManager`:
```bash
python cli.py auth generate-key --client-id "prod-trading-engine-01" --role ADMIN
# Output:
# API Key: mdrap_live_9f83a8... (Store securely in HashiCorp Vault or AWS Secrets Manager)
```

### 2. Reverse Proxy TLS Termination (Caddy Example)
Deploy Caddy or Nginx in front of MDRAP to enforce TLS 1.3, strict rate limiting, and HTTP/2:
```caddy
trading.internal.firm.com {
    tls /etc/ssl/certs/trading.crt /etc/ssl/certs/trading.key {
        protocols tls1.3
    }
    
    # Enforce API rate limiting
    rate_limit {
        zone dynamic_api {
            key {header.X-API-Key}
            events 5000
            window 1s
        }
    }

    reverse_proxy 127.0.0.1:8000 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

---

## 6. Commercial Feed Boundary Verification

Before deploying equities or futures integrations:
1. **Polygon.io (Equities/Options)**:
   - Verify `POLYGON_API_KEY` is set in the environment.
   - Test connectivity:
     ```bash
     python -c "from mdrap.adapters.polygon import PolygonFeedManager; mgr = PolygonFeedManager(['AAPL']); print('Auth initialized')"
     ```
   - If key is missing or invalid, MDRAP strictly raises `ValueError` and prevents silent mock fallback.
2. **Databento (CME Futures/Equities)**:
   - Verify `DATABENTO_API_KEY` is set in the environment.
   - If key or binary recording is missing, `DatabentoFeedManager` strictly raises `ValueError`.

---

## 7. Monitoring, Metrics & Alert Thresholds

MDRAP exposes real-time telemetry via Prometheus `/metrics`. Configure Prometheus scrape jobs with the following institutional alerts:

| Metric Name | Warning Threshold | Critical Threshold | Action Required |
|---|---|---|---|
| `mdrap_queue_depth` | > 2,000 events | > 8,000 events | Ingress backpressure. Scale consumer threads or optimize persistence. |
| `mdrap_dropped_tombstones_total`| > 0 | > 10 | Packet drops under queue saturation. Check consumer bottlenecks. |
| `mdrap_source_status` | `DEGRADED` (1 feed) | `SILENT` (All feeds) | Feed disconnection or exchange outage. Check WAN link and DNS. |
| `mdrap_wal_fsync_latency_seconds` | > 0.005 s (5 ms) | > 0.050 s (50 ms) | Disk I/O contention on NVMe volume. Check disk saturation. |
| `mdrap_bbo_spread_dollars` | < 0 (Crossed) | Crossed > 50 ms | Cross-venue anomaly or crossed market condition. Quarantined automatically. |
| `mdrap_auth_failures_total` | > 5 in 1 min | > 20 in 1 min | Potential brute-force attack on API key. Check client source IP. |

---

## 8. Pre-Flight Release Gate Checklist

Before opening network ports to downstream trading strategies, verify each item:

- [ ] `MDRAP_ENV` is set to `production`.
- [ ] `MDRAP_API_KEY_SALT` is set to a secure 64-character random string (non-demo).
- [ ] `MDRAP_FSYNC_POLICY` is set to `grouped_by_time` with `MDRAP_FSYNC_INTERVAL_MS=50`.
- [ ] All 1,735 unit and integration tests pass cleanly (`pytest tests/ -v`).
- [ ] Native C fastpath acceleration is active (`is_available() == True`).
- [ ] Reverse proxy (Caddy/Nginx) terminates TLS 1.3 on port 443 and proxies to `127.0.0.1:8000`.
- [ ] Permissions on WAL and SQLite storage are locked down to `0700` / `0600`.
- [ ] Live WebSocket feeds (Coinbase, Binance, Kraken, OKX, Bybit) connect and stream valid quotes.
- [ ] Prometheus metrics scraper is operational and scraping `/metrics`.
- [ ] Source watchdog heartbeat is active and monitoring feed silence (`silence_threshold_s=3.0`).

**Release Authorization**: When all checklist items are checked, MDRAP v5 is cleared for live institutional deployment.
