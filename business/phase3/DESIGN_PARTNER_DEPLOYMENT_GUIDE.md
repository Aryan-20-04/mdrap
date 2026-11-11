# MDRAP Design Partner Deployment & Operations Guide

**Product Version**: MDRAP Institutional Release  
**Auditor / Author**: Agent 7 — Product Deployment & Infrastructure Engineering  
**Date**: 2026-11-11  
**Deployment Target**: Linux (Ubuntu 22.04+ / RHEL 9+) or Windows Server (AMD64)  
**Architecture Role**: Passive, Zero-Execution Market Data Reliability & Assurance Sidecar  

---

## 1. System Requirements & Architecture Overview

MDRAP operates strictly **alongside** your trading infrastructure as a passive observer. It never touches order routing, never handles private execution keys, and never injects latency into your trading path.

```
                      +-----------------------------+
                      |   Public Market Feeds       |
                      | (Binance, Coinbase, Kraken) |
                      +--------------+--------------+
                                     |
               +---------------------v---------------------+
               |       MDRAP Reliability Sidecar           |
               |                                           |
               |  1. Passive Ingestion & Frame Normalization|
               |  2. 15-Rule Deterministic Quality Engine  |
               |  3. SHA-256 Provenance & Lineage Hashing  |
               |  4. Durable SQLite WAL Event Store        |
               |  5. RFC 6962 Merkle Incident Anchoring    |
               +---------------------+---------------------+
                                     |
        +----------------------------+----------------------------+
        |                                                         |
+-------v-------------------------+      +------------------------v------------------------+
| Read-Only Observability & Alert |      | Standalone Forensic Evidence Export             |
| (REST / WebSocket / CLI / TUI)  |      | (Independent Merkle Verification for Disputes)  |
+---------------------------------+      +-------------------------------------------------+
```

### Hardware & Resource Allocation
- **CPU**: 1 dedicated physical core or 2 vCPUs.
- **RAM**: 512 MB to 1 GB RAM (Sidecar RSS footprint is < 50 MB under 10k ticks/sec).
- **Disk**: 20 GB to 100 GB NVMe SSD (WAL mode; requires ~1.2 GB per 5,000,000 ticks).
- **Network**: Outbound HTTPS/WSS (Port 443) to configured exchange public endpoints. No inbound open ports required.

---

## 2. Installation Instructions

### Option A: Clean Virtual Environment Installation (Recommended)
```bash
# 1. Create dedicated isolated environment
python3 -m venv /opt/mdrap/venv
source /opt/mdrap/venv/bin/activate

# 2. Install MDRAP distribution package
pip install --upgrade pip
pip install mdrap_core-5.3.0-py3-none-any.whl

# 3. Optional: Install extras for REST API and Rich UI
pip install "mdrap-core[api,ui,feeds]"
```

### Option B: Docker Container Deployment
```bash
docker run -d \
  --name mdrap-sidecar \
  --restart unless-stopped \
  -v /var/lib/mdrap/data:/data \
  -v /etc/mdrap/config.toml:/etc/mdrap/config.toml:ro \
  -e MDRAP_CONFIG=/etc/mdrap/config.toml \
  -e MDRAP_STORAGE_PATH=/data/mdrap.db \
  mdrap:latest
```

---

## 3. Configuration Template (`config.toml`)

Create `/etc/mdrap/config.toml`:

```toml
[platform]
node_id = "pilot-node-01"
log_level = "INFO"
read_only_enforced = true

[storage]
database_path = "/data/mdrap_pilot.db"
wal_mode = true
synchronous = "NORMAL"
cache_size_kb = 65536
retention_days = 14

[security]
# Required: Non-demo API key salt for cryptographic hashing
api_key_salt = "REPLACE_WITH_A_SECURE_RANDOM_32_CHAR_SALT_STRING"
enable_read_only_boundary = true

[feeds]
# Public feeds to passively monitor
providers = ["COINBASE", "KRAKEN", "BINANCE"]
instruments = ["BTC-USD", "ETH-USD", "SOL-USD"]
reconnect_backoff_max_s = 30.0
heartbeat_interval_s = 10.0

[quality]
# Anomaly detection thresholds
inverted_spread_threshold = 0.0
price_jump_percentage = 5.0
stale_quote_timeout_ms = 500.0
max_clock_drift_ms = 1000.0
sequence_gap_alert = true
duplicate_rejection = true

[incidents]
auto_quarantine = true
corridor_window_seconds = 15.0
merkle_anchoring = true
export_dir = "/data/incidents"
```

---

## 4. Verification & Health Checks

### System Health & Diagnostics
```bash
# Check connectivity, database status, and configuration
mdrap check

# Verify internal components and system readiness
mdrap doctor
```

### Live Passive Monitoring
```bash
# Stream live ticks and observe anomaly rates in real time
mdrap live BTC-USD --feed crypto --limit 50

# View connector health telemetry and frame arrival metrics
mdrap telemetry
```

---

## 5. Incident Investigation & Forensics

When an anomaly occurs (e.g. sequence drop, crossed quote, feed gap):

### 1. Query Active Incidents
```bash
mdrap incident list
```
Example Output:
```
ID                   SEVERITY   ANOMALY TYPE     VENUE      INSTRUMENT   DETECTED AT
inc-1794415426-0001  HIGH       SEQUENCE_GAP     COINBASE   BTC-USD      2026-11-11T16:43:46Z
```

### 2. Inspect Forensic Details & Replay Corridor
```bash
# Deterministically replay ±15 seconds around the incident
mdrap replay inc-1794415426-0001
```

### 3. Export Standalone Cryptographic Evidence Package
```bash
# Export evidence package with RFC 6962 Merkle inclusion proofs
mdrap export inc-1794415426-0001 -o /data/incidents/evidence_inc-0001.json
```

### 4. Standalone Verification (No DB Access Needed)
Counterparties or internal auditors can verify authenticity independently:
```bash
python -m mdrap.historical_verifier /data/incidents/evidence_inc-0001.json
# Output: [VERIFIED] Cryptographic Merkle Root Match: True | Integrity: 100%
```

---

## 6. Backup, Maintenance & Recovery

### Zero-Downtime Hot Backup
```bash
# Online SQLite backup with cryptographic Merkle audit verification
python scripts/backup.py --db /data/mdrap_pilot.db --target /backup/mdrap_backup.db.gz --compress
```

### Pre-Restore Verification & Restore
```bash
# Restore with pre-restore safety snapshot
python scripts/restore.py --source /backup/mdrap_backup.db.gz --target /data/mdrap_pilot.db
```

---

## 7. Troubleshooting & Recovery Procedures

| Symptom | Probable Cause | Corrective Action |
| :--- | :--- | :--- |
| `API key salt not configured` | Missing `MDRAP_API_KEY_SALT` | Export `MDRAP_API_KEY_SALT=<salt>` in environment or `config.toml`. |
| `SQLite database locked` | High write contention | Ensure WAL mode is active (`PRAGMA journal_mode=WAL;`). |
| `WebSocket connection timeout` | Outbound firewall blocking 443 | Verify host has outbound HTTPS/WSS access to `wss://ws-feed.exchange.com`. |
| `Torn frame warning on restart` | Sudden ungraceful host shutdown | IngestLog automatically truncates torn tail frame. Run `mdrap check` to confirm clean recovery. |

---

## 8. Uninstallation & Rollback

Because MDRAP is a passive sidecar, removal has **zero risk** to your trading systems:
```bash
# 1. Stop service
systemctl stop mdrap-sidecar || docker stop mdrap-sidecar

# 2. Remove package
pip uninstall -y mdrap-core

# 3. Data retention
# Database files in /data/ can be archived or safely purged.
```
