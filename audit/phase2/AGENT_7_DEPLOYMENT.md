# AGENT 7 — Institutional Customer Deployment & Operations Runbook

**Document Version**: 1.0.0 (Pilot Candidate)  
**Target Platform**: Linux (Ubuntu 22.04 LTS / RHEL 9) & Windows Server 2022 / Windows 11  
**Architecture**: MDRAP Sidecar Deployment Model  

---

## 1. Architectural Positioning

MDRAP is designed as a zero-interference, independent reliability sidecar positioned alongside institutional trading systems (OMS, EMS, pricing engines, backtesting databases).

```
                      +-----------------------------+
                      |   Market Data Exchanges     |
                      |  (Coinbase, Binance, CME)   |
                      +--------------+--------------+
                                     |
                                     | (Public Multicast / WebSockets)
                                     v
+------------------------+                     +------------------------+
| Institutional OMS/EMS  |                     |      MDRAP SIDECAR     |
| (Trading Engines, Alg) | <--- (Optional SHM) | (Independent Assurance)|
|                        |                     |                        |
| - Executes Orders      |                     | - Read-Only Ingestion  |
| - Manages Capital      |                     | - Microsecond Quality  |
| - Zero Audit Overhead  |                     | - Cryptographic Merkle |
+------------------------+                     | - Forensics & Replay   |
                                               +-----------+------------+
                                                           |
                                                           v
                                               +------------------------+
                                               |  Durable NVMe Storage  |
                                               | (SQLite WAL / Ingest)  |
                                               +------------------------+
```

### Key Deployment Guarantees:
1. **Passive Sniffing / Feed Mirroring**: MDRAP subscribes to read-only market data feeds or receives mirrored UDP/TCP packets. It requires **zero order-entry API keys** and has **no execution logic**.
2. **Resource Isolation**: Runs in its own process space with hard CPU affinity and memory limits, ensuring zero GC pauses or compute contention for core trading algorithms.
3. **Low-Latency Inter-Process Communication (IPC)**: When co-located on the same server, MDRAP can publish canonical validated ticks via POSIX / Windows Shared Memory (`shm.py`) with sub-microsecond transfer times.

---

## 2. Infrastructure Prerequisites

| Component | Minimum Requirement | Recommended Production Specification |
| :--- | :--- | :--- |
| **Operating System** | Linux (Kernel $\ge 5.15$) or Windows 10/11 / Server 2022 | Linux Ubuntu 22.04 LTS with `taskset` / `cgroups` |
| **CPU Architecture** | x86_64 (SSE4.2 support) | x86_64 with AVX2 support (e.g. AMD EPYC 7003+ or Intel Xeon 3rd Gen+) |
| **Python Runtime** | Python 3.11+ | Python 3.13 (Native C Fastpath enabled) |
| **Storage Subsystem** | 20 GB SSD | High-IOPS NVMe SSD (PCIe 4.0/5.0), formatted with ext4/XFS or NTFS |
| **RAM** | 4 GB | 16 GB ECC RAM (allocating 2 GB for memory-mapped ring buffers) |

---

## 3. Storage & Memory Sizing Guidelines

### Storage Sizing Model:
- **Raw Ingest WAL**: Binary format is ~180 bytes per raw market frame.
  - $1,000\text{ ticks/sec} \approx 180\text{ KB/sec} \approx 648\text{ MB/hour} \approx 15.5\text{ GB/day}$.
- **Event Provenance (SQLite WAL)**: Compressed row with SHA-256 digests and quality metadata is ~220 bytes.
  - Total sustained disk throughput at $10,000\text{ ticks/sec} \approx 4\text{ MB/sec}$, easily sustained by standard NVMe write cycles.

### Memory Sizing Model:
- Standard In-Memory Ring Buffer: 100,000 slots $\times$ 256 bytes $\approx 25.6\text{ MB}$.
- Process RSS footprint: Base python runtime ~35 MB + active ring buffer ~26 MB $\approx 60\text{ MB}$ steady-state.

---

## 4. Step-by-Step Installation & Verification

### Step 4.1: Environment Setup
```bash
# Clone the repository
git clone https://github.com/organization/mdrap.git
cd mdrap
git checkout product-focus/mdrap-reliability-platform

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Step 4.2: Native Acceleration Compilation (Optional but Recommended)
```bash
# Build native C kernel for AVX2 fastpath
python setup.py build_ext --inplace
```
*Verification*: Run `python -c "from mdrap.fastpath import is_available; print('Native Fastpath Active:', is_available())"`.

### Step 4.3: Smoke Test Suite
```bash
# Verify core functionality and security boundaries
pytest tests/test_phase2_security_profile.py tests/test_phase2_resilience.py -v
```

---

## 5. Configuration Guide

MDRAP configuration is specified via `config/reliability.json` or environment variables:

```json
{
  "venues": ["COINBASE", "KRAKEN"],
  "symbols": ["BTC-USD", "ETH-USD"],
  "storage": {
    "db_path": "data/mdrap_pilot.db",
    "wal_mode": true,
    "sync_mode": "NORMAL",
    "retention_days": 14
  },
  "quality": {
    "max_future_skew_s": 5.0,
    "staleness_threshold_s": 10.0,
    "max_spread_pct": 0.05,
    "session_policy": "auto_detect"
  },
  "assurance": {
    "merkle_anchor_interval_s": 60,
    "lineage_hash_algorithm": "SHA256",
    "export_format": "JSON_STANDALONE"
  }
}
```

---

## 6. Incident Investigation & Independent Evidence Verification

When a market event triggers a dispute or data quality anomaly:

### Step 6.1: Query Incidents via CLI
```bash
python cli.py incident list --db data/mdrap_pilot.db --status OPEN
```

### Step 6.2: Replay Incident Window Deterministically
```bash
python cli.py replay --incident-id INC-20261111-001 --corridor-s 30 --output replay_summary.json
```

### Step 6.3: Independent Zero-Dependency Verification
Export the incident package and run the standalone verification tool (requires only standard Python `hashlib` and `hmac`):
```bash
python tools/verify_evidence.py --package incident_INC-20261111-001.json
```
**Expected Verification Output**:
```text
[OK] Event ID: evt-001 | SHA-256 Wire Digest Verified: MATCH
[OK] Event ID: evt-001 | Lineage Hash Verified: MATCH
[OK] Merkle Inclusion Proof: VALID against Root Anchor 3f7a1c...
AUDIT RESULT: TAMPER-FREE EVIDENCE CONFIRMED.
```

---

## 7. Monitoring, Health Checks & Observability

### Health CLI Command
```bash
python cli.py health --db data/mdrap_pilot.db
```
Returns JSON health report:
```json
{
  "status": "HEALTHY",
  "venues": {
    "COINBASE": {"state": "HEALTHY", "staleness_ms": 142.3, "gaps": 0},
    "KRAKEN": {"state": "HEALTHY", "staleness_ms": 310.1, "gaps": 0}
  },
  "ingest_rate_eps": 482.0,
  "uncommitted_tombstones": 0
}
```

---

## 8. Backup, Disaster Recovery & Rollback

### SQLite WAL Backup Procedure
Run online backup without stopping ingestion:
```bash
sqlite3 data/mdrap_pilot.db ".backup 'data/backups/mdrap_backup_$(date +%Y%m%d_%H%M%S).db'"
```

### Rollback Procedure
If the sidecar needs to be downgraded or removed:
1. Stop the sidecar process: `kill -TERM <pid>`.
2. Existing trading systems are unaffected because they receive market data independently.
3. To purge database files: remove `data/mdrap_pilot.db*`.
