# MDRAP Phase 3 — Enterprise Deployment Guide

**Document Version**: 1.0.0  
**Target Product**: MDRAP — Market Data Reliability & Assurance Platform  
**Author**: Lead Infrastructure & Deployment Engineer (Agent 5)  
**Status**: APPROVED FOR CUSTOMER DESIGN PARTNERS  

---

## 1. Architectural Overview & Placement

MDRAP is designed to be installed as an **independent, zero-interference sidecar** running on the same Linux or Windows server as the customer's market-data ingestion layer, or in an adjacent isolated container.

```
                              [ Exchange Feeds ]
                                      |
                      +---------------+---------------+
                      | (Public WebSockets / Direct)  |
                      v                               v
          +-----------------------+       +-----------------------+
          |  TRADING ENGINE / OMS |       |     MDRAP SIDECAR     |
          |  (Customer Codebase)  |       |  (Independent Audit)  |
          |                       |       |                       |
          | - Routes Orders       |       | - Read-Only Passive   |
          | - Holds Private Keys  |       | - Microsecond Quality |
          | - Critical Trading Path       | - Deterministic Replay|
          +-----------------------+       | - RFC 6962 Merkle Log |
                                          +-----------+-----------+
                                                      |
                                                      v
                                          +-----------------------+
                                          | Local NVMe WAL Storage|
                                          +-----------------------+
```

### Core Deployment Assurances:
1. **Zero Order Execution**: MDRAP contains no order-routing logic, no trade execution APIs, and requires zero private trading credentials.
2. **Process Isolation**: Operates in its own process space with dedicated memory boundaries; cannot crash or block customer trading engines.
3. **Fail-Open Resilience**: If the MDRAP process terminates, customer trading operations continue without interruption.

---

## 2. Supported Deployment Models

### Option A: Local Linux Systemd Service (Recommended for Low Latency)
- **Target OS**: Ubuntu 22.04 LTS / Debian 12 / RHEL 9 / Rocky Linux 9.
- **Resource Footprint**: 1 vCPU core, 1 GB RAM, NVMe storage partition.
- **Installation Footprint**: Standard Python virtual environment with optional compiled C hot-path (`_fastpath_native.so`).

### Option B: Docker Containerized Sidecar (Recommended for Fast Pilots)
- **Target Platform**: Docker Engine 24.0+ / Podman / Docker Compose.
- **Security Profile**: `read_only: true` root filesystem, non-root user (`uid=10001`), no network capabilities except outbound HTTPS/WSS to public exchange endpoints.

### Option C: Bare-Metal Windows Server (for Windows-Based Quantitative Desks)
- **Target OS**: Windows Server 2022 / Windows 11 Enterprise.
- **Support**: Native Windows shared memory (`shm.py`) and pre-compiled DLL acceleration.

---

## 3. Step-by-Step Installation Procedure (Linux / Systemd)

### Step 3.1: System Prerequisites
```bash
# Verify Python version (3.11+ required)
python3 --version

# Install SQLite 3.35+ and build tools
sudo apt-get update && sudo apt-get install -y python3-venv python3-dev build-essential sqlite3
```

### Step 3.2: Create Dedicated Service User
```bash
# Create restricted unprivileged user
sudo useradd -r -s /bin/false -d /opt/mdrap mdrap-svc
sudo mkdir -p /opt/mdrap /var/log/mdrap /var/lib/mdrap/data
sudo chown -R mdrap-svc:mdrap-svc /opt/mdrap /var/log/mdrap /var/lib/mdrap
```

### Step 3.3: Deploy Application & Virtual Environment
```bash
cd /opt/mdrap
sudo -u mdrap-svc git clone https://github.com/organization/mdrap.git .
sudo -u mdrap-svc python3 -m venv .venv
sudo -u mdrap-svc .venv/bin/pip install --upgrade pip
sudo -u mdrap-svc .venv/bin/pip install -r requirements.txt

# Compile native C SIMD/fastpath hot-path (optional, falls back cleanly to pure Python)
sudo -u mdrap-svc .venv/bin/python setup.py build_ext --inplace
```

### Step 3.4: Configuration Setup
Create `/opt/mdrap/config/production.json` with permissions `0600`:
```json
{
  "venues": ["COINBASE", "KRAKEN"],
  "symbols": ["BTC-USD", "ETH-USD"],
  "storage": {
    "db_path": "/var/lib/mdrap/data/mdrap_live.db",
    "wal_mode": true,
    "sync_mode": "NORMAL",
    "retention_days": 14
  },
  "quality": {
    "max_future_skew_s": 5.0,
    "staleness_threshold_s": 10.0,
    "reorder_window_s": 0.05
  },
  "assurance": {
    "merkle_anchor_interval_s": 60,
    "export_format": "JSON_STANDALONE"
  }
}
```

### Step 3.5: Systemd Unit Configuration
Create `/etc/systemd/system/mdrap.service`:
```ini
[Unit]
Description=MDRAP Market Data Reliability & Assurance Platform
After=network.target

[Service]
Type=simple
User=mdrap-svc
Group=mdrap-svc
WorkingDirectory=/opt/mdrap
ExecStart=/opt/mdrap/.venv/bin/python cli.py run --config /opt/mdrap/config/production.json
Restart=always
RestartSec=5s
LimitNOFILE=65536
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mdrap /var/log/mdrap

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable --now mdrap.service
sudo systemctl status mdrap.service
```

---

## 4. Verification & Smoke Test

Confirm operational health immediately after installation:
```bash
# Query live health status
/opt/mdrap/.venv/bin/python cli.py health --db /var/lib/mdrap/data/mdrap_live.db

# Run repeatable demonstration / smoke test
/opt/mdrap/.venv/bin/python tools/run_customer_demo.py
```
Expected output: All 12 demonstration steps execute with `[OK]` status.
