# MDRAP Operational Architecture & Runbook Index

This document serves as the high-level operational guide for running MDRAP in institutional production environments.

---

## 1. Operational Overview

MDRAP is designed to run as a single-node sidecar process on bare metal, Linux VMs, or containerized environments. It enforces continuous health monitoring, automated crash recovery from WAL segments, and streaming replication for active-passive standby failover.

---

## 2. Service Management

### Running via Systemd (Linux)
Create `/etc/systemd/system/mdrap.service`:

```ini
[Unit]
Description=MDRAP Market Data Platform
After=network.target

[Service]
Type=simple
User=mdrap
Group=mdrap
WorkingDirectory=/var/lib/mdrap
Environment="MDRAP_API_KEY_SALT=your-stable-32-byte-hex-salt"
Environment="MDRAP_CONFIG_FILE=/etc/mdrap/mdrap.toml"
ExecStart=/usr/local/bin/mdrap serve --host 127.0.0.1 --port 8000 --db /var/lib/mdrap/data/mdrap.db
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### Running via Docker Compose
MDRAP includes an institutional multi-stage Docker configuration:

```bash
# Start MDRAP sidecar with Prometheus exporter
docker compose up -d
```

---

## 3. Telemetry & Alerting

### Prometheus Metrics (`/metrics`)
MDRAP exposes Prometheus metrics for operational observability:
- `mdrap_events_total`: Total market events processed.
- `mdrap_invalid_events_total`: Quarantined events count.
- `mdrap_wal_fsync_duration_ms`: Disk persistence latency.
- `mdrap_shm_drops_total`: Frame evictions due to downstream subscriber backpressure.

### Health Probes
- Liveness: `GET /v1/liveness` — confirms internal thread liveness.
- Readiness: `GET /v1/readiness` — confirms WAL segment write access and SQLite availability.

---

## 4. Runbooks & Disaster Recovery

- **[Disaster Recovery Runbook](operations/DISASTER_RECOVERY.md)**: Procedures for restoring corrupt SQLite databases from raw Write-Ahead Log segments.
- **[Failover Runbook](operations/FAILOVER_RUNBOOK.md)**: Manual and automated promotion of standby streaming replicas.
- **[SLO & Metrics Specifications](operations/SLO_AND_METRICS.md)**: Target service level objectives (p99 latency, zero uncounted drops).
- **[Backup & Restore Guide](backup-restore.md)**: Taking live point-in-time SQLite backups using `VACUUM INTO` and segment archiving.
