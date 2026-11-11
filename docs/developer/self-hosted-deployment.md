# Self-Hosted Deployment Guide

This guide provides operational instructions for deploying the MDRAP Market Data Reliability & Assurance Platform as a self-hosted read-only sidecar on institutional infrastructure.

---

## 1. Architecture & Design Principles

MDRAP operates as an independent, read-only observer:
- **Zero Order Execution**: Contains no order routing, trading logic, private API keys, or execution interfaces.
- **Passive Monitoring**: Connects to public exchange market-data streams via WebSocket or multicast network taps.
- **Local Persistence**: Stores forensic event provenance, data-quality incidents, and RFC 6962 Merkle proofs in an embedded SQLite database configured with Write-Ahead Logging (WAL).
- **Isolation**: Runs under a dedicated, unprivileged user account (`mdrap`, UID 10001) with all Linux capabilities dropped.

---

## 2. Hardware Sizing & Capacity Planning

Resource requirements depend on the number of monitored feeds and aggregate event throughput:

| Tier | Throughput (ticks/sec) | Monitored Instruments | CPU Cores | RAM | Storage / Day (WAL) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Pilot / Dev** | Up to 5,000 | 1 - 5 | 2 vCPU | 2 GB | ~2 GB / day |
| **Standard Production** | Up to 25,000 | 5 - 20 | 4 vCPU | 4 GB | ~8 GB / day |
| **High-Volume / FX / Crypto** | Up to 100,000 | 20 - 50 | 8 vCPU | 8 GB | ~30 GB / day |

### Disk Recommendations
- Use high-IOPS NVMe or SSD storage for the SQLite WAL directory.
- Avoid network-attached filesystems (NFS, SMB) for SQLite database files due to file-locking latency and corruption risks during network partitions.

---

## 3. Pre-Flight Environment Validation

Before installing, run the pre-flight environment validator to verify host compatibility:

```bash
python deploy/validate_environment.py --data-dir /var/lib/mdrap
```

Output:
```text
======================================================================
  MDRAP Self-Hosted Environment Validation Pre-Flight Check
======================================================================
  [PASS] Python Runtime            : Python 3.11.8 (Supported)
  [PASS] SQLite & WAL Engine       : SQLite 3.45.3 [WAL Mode: WAL]
  [PASS] Storage Sizing            : Disk space adequate: 120.40 GB free
  [PASS] Acceleration Engine       : Native C SIMD/fastpath kernel ACTIVE
  [PASS] Read-Only Safety Barrier  : Verified: Zero execution credentials present
======================================================================
  Environment validation successful. Host is ready for MDRAP sidecar.
======================================================================
```

---

## 4. Deployment Option A: Docker / Container Compose

### Step 1: Clone and Configure
```bash
git clone https://github.com/mdrap-assurance/mdrap.git
cd mdrap
mkdir -p deploy/data
cp deploy/config.example.toml deploy/config.toml
```

### Step 2: Edit Configuration
Edit `deploy/config.toml` to specify your target instruments and providers. Ensure `salt` is populated with a cryptographically secure 32-byte hex string:
```bash
openssl rand -hex 32
```

### Step 3: Launch with Docker Compose
```bash
cd deploy
docker compose up -d
```

### Step 4: Verify Container Health
```bash
docker compose ps
docker compose logs -f
```

---

## 5. Deployment Option B: Bare-Metal / Virtual Machine (systemd)

For low-latency bare-metal environments:

### Step 1: Create Dedicated User and Directories
```bash
sudo useradd -r -s /usr/sbin/nologin -d /var/lib/mdrap mdrap
sudo mkdir -p /var/lib/mdrap /var/log/mdrap /etc/mdrap
sudo chown -R mdrap:mdrap /var/lib/mdrap /var/log/mdrap /etc/mdrap
```

### Step 2: Install MDRAP Package
```bash
sudo pip install mdrap_core-6.0.0-py3-none-any.whl
sudo cp deploy/config.example.toml /etc/mdrap/config.toml
```

### Step 3: Install and Enable systemd Unit
```bash
sudo cp deploy/mdrap.service /etc/systemd/system/mdrap.service
sudo systemctl daemon-reload
sudo systemctl enable --now mdrap.service
```

### Step 4: Monitor Service Status
```bash
sudo systemctl status mdrap.service
sudo journalctl -u mdrap.service -f
```

---

## 6. Backup and Recovery Runbooks

### Online Hot Backup
SQLite WAL mode allows zero-downtime hot backups using the SQLite backup API:
```bash
python scripts/backup.py --db /var/lib/mdrap/mdrap.db --dest /backups/mdrap-$(date +%F).db
```

### Disaster Recovery
To recover from a host failure:
1. Provision a new host running Python >= 3.10.
2. Restore the database file to `/var/lib/mdrap/mdrap.db`.
3. Start the service. SQLite will automatically replay the WAL journal if uncommitted transactions exist.
4. Verify database consistency:
```bash
mdrap check --db /var/lib/mdrap/mdrap.db
```

---

## 7. Uninstallation & Data Retention

To remove MDRAP from a host:
```bash
# Docker
cd deploy && docker compose down -v

# systemd
sudo systemctl stop mdrap.service
sudo systemctl disable mdrap.service
sudo rm /etc/systemd/system/mdrap.service
sudo systemctl daemon-reload
```

Data in `/var/lib/mdrap` is retained and can be archived or transferred to a cold forensic storage tier.
