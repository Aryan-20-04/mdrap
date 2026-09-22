# MDRAP Production Deployment Guide

This document outlines best practices and architectural requirements for deploying MDRAP as self-hosted software in enterprise environments.

---

## 1. Architectural Overview

MDRAP is designed to run self-hosted on customer-owned infrastructure (bare-metal servers, private cloud instances, or dedicated on-premise compute nodes):

```
+-------------------------------------------------------------------------+
| Customer Secure Network Boundary                                        |
|                                                                         |
|  [ Inbound Vendor Feeds ] ---> [ MDRAP Ingestion & Normalization ]      |
|  (Polygon, CME, Binance)                 |                              |
|                                          v                              |
|                            [ Quality & Anomaly Engine ]                 |
|                                          |                              |
|                                          v                              |
|                          [ Cross-Feed Reconciler & BBO ]                |
|                                   /              \                      |
|                                  v                v                     |
|           [ Persistent Storage ]            [ Real-Time Distribution ]   |
|           (SQLite WAL / DuckDB)             (FastAPI REST + WebSocket)  |
|                     ^                                 |                 |
|                     |                                 v                 |
|         [ Online WAL Backups ]                [ Trading Desks & Quants ]|
+-------------------------------------------------------------------------+
```

### Self-Hosted Philosophy
- **Single Commercial Footprint**: No multi-tenant complexity, billing engines, or cloud SaaS dependencies.
- **Embedded Storage**: High-performance SQLite operating in Write-Ahead Log (WAL) mode with zero external database processes required.
- **Low-Latency In-Memory Tiers**: POSIX Shared Memory (SHM) ring buffers for sub-microsecond IPC on the local host.

---

## 2. Infrastructure Sizing & Hardware Recommendations

| Workload Tier | Daily Events | CPU Cores | RAM | Storage Type | Recommended Instance |
|---|---|---|---|---|---|
| **Standard Evaluation** | < 5,000,000 | 4 cores | 8 GB | Standard SSD | AWS `c6i.xlarge` / GCP `c2-standard-4` |
| **Institutional Desk** | 5M – 50M | 8–16 cores | 32 GB | NVMe SSD | AWS `c6i.4xlarge` / GCP `c2-standard-16` |
| **High-Frequency Multi-Venue**| > 100,000,000 | 32+ cores | 64 GB+ | Direct-Attached NVMe | Dedicated Bare-Metal with Linux kernel >= 6.0 |

### Storage Performance
- **Filesystem**: `ext4` or `xfs` with `noatime` mount option recommended on Linux.
- **SQLite WAL**: SQLite writes are batched using `executemany` with `PRAGMA synchronous = NORMAL`. NVMe drives ensure checkpoint operations complete in single-digit milliseconds.

---

## 3. Container Deployment (Docker Compose)

The production `docker-compose.yml` provides a hardened deployment:

```yaml
version: "3.8"

services:
  mdrap-core:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: mdrap-core
    restart: unless-stopped
    ports:
      - "8000:8000"   # REST & WebSocket API
      - "9001:9001"   # Ultra-low-latency TCP wire protocol
    environment:
      - PYTHONUNBUFFERED=1
      - MDRAP_HOST=0.0.0.0
      - MDRAP_PORT=8000
      - MDRAP_DB_PATH=/data/mdrap.db
      - MDRAP_INITIAL_ADMIN_KEY=${MDRAP_INITIAL_ADMIN_KEY:-}
      - MDRAP_CORS_ORIGINS=${MDRAP_CORS_ORIGINS:-*}
    volumes:
      - mdrap-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8000/v1/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  mdrap-data:
    name: mdrap-data
```

### Volume Management
- The persistent database file is stored inside `/data/mdrap.db`.
- Back up the volume using standard volume snapshots or the included online backup utility (`scripts/backup.py`).

---

## 4. TLS Termination via Reverse Proxy

To enable automated HTTPS and TLS 1.3 termination using the included Caddy configuration:

1. Configure `DOMAIN` in `.env`:
   ```bash
   DOMAIN=mdrap.internal.firm.com
   ```
2. Start containers with the `tls` profile enabled:
   ```bash
   docker compose --profile tls up -d
   ```

Caddy will automatically provision internal or public certificates (via Let's Encrypt or internal ACME CA) and proxy all WebSocket and HTTP traffic to `mdrap-core:8000`.

---

## 5. Environment Variables Reference

| Variable | Default | Purpose |
|---|---|---|
| `MDRAP_HOST` | `0.0.0.0` | Host IP for API binding. |
| `MDRAP_PORT` | `8000` | Port for REST & WebSocket service. |
| `MDRAP_DB_PATH` | `data/mdrap.db` | Path to persistent SQLite database file. |
| `MDRAP_INITIAL_ADMIN_KEY` | *(auto-generated)* | Bootstrap master ADMIN API token on first boot. |
| `MDRAP_CORS_ORIGINS` | `*` | Allowed CORS origin domains (comma-separated). |
| `POLYGON_API_KEY` | `""` | Direct API key for Polygon.io market data feed. |
| `DATABENTO_API_KEY` | `""` | Direct API key for Databento market data feed. |
| `COINBASE_API_KEY` | `""` | Direct API credentials for Coinbase Pro feed. |
| `BINANCE_API_KEY` | `""` | Direct API credentials for Binance WebSocket feed. |
