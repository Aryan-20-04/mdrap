# Operations & Maintenance Guide

## Goal
Deploy, configure, monitor, and maintain MDRAP in production environments, ensuring service health, high availability via active-passive WAL streaming, and zero silent data corruption.

## Prerequisites
- Python 3.10+
- Production host or container environment (Linux x86-64 / ARM64 or Windows Server)
- Core package and optional API dependencies installed: `pip install -e ".[api]"`

## Steps

### Step 1: Secure Configuration
Configure cryptographic environment variables before launching the service:

```bash
# Generate a cryptographically secure 32-byte salt
export MDRAP_API_KEY_SALT=$(python -c "import secrets; print(secrets.token_hex(16))")
export MDRAP_INITIAL_ADMIN_KEY="prod-admin-bootstrap-token"

# Optional path to system configuration file
export MDRAP_CONFIG_FILE="/etc/mdrap/mdrap.toml"
```

### Step 2: Launch the API Daemon
Start the production service listening on loopback or your private overlay network:

```bash
mdrap serve --host 127.0.0.1 --port 8000 --db /var/lib/mdrap/mdrap.db
```

### Step 3: Monitor Health & Liveness
Configure container orchestrators or monitoring agents to query the standardized health endpoints:

```bash
# Liveness probe (verifies process responsiveness)
curl -f http://127.0.0.1:8000/v1/liveness

# Readiness probe (verifies WAL write access and SQLite availability)
curl -f http://127.0.0.1:8000/v1/readiness

# Comprehensive health status
curl -s http://127.0.0.1:8000/v1/health
```

### Step 4: Scrape Prometheus Metrics
MDRAP exposes Prometheus-compatible metrics on `/metrics`:

```bash
curl -s http://127.0.0.1:8000/metrics
```

Key operational metrics include:
- `mdrap_events_ingested_total`: Total events received at the gateway.
- `mdrap_quality_invalid_total`: Count of quarantined invalid events.
- `mdrap_wal_fsync_duration_seconds`: Histogram of disk sync latency.
- `mdrap_shm_drop_count`: Counter for socket/ring backpressure evictions.

### Step 5: Active-Passive WAL Replication
To establish an active-passive standby instance, run the streaming journal replicator daemon (`src/mdrap/journal_replicator.py`):

```bash
# Standby node: listen for streaming WAL chunks
python -m mdrap.journal_replicator --mode standby --bind 0.0.0.0:9990 --target-dir /var/lib/mdrap/wal/

# Primary node: stream WAL segments to standby
python -m mdrap.journal_replicator --mode primary --peer 10.0.0.2:9990 --wal-dir /var/lib/mdrap/wal/
```

### Step 6: Run Environment Diagnostics
Execute the built-in diagnostic tool to verify compiler acceleration and hardware profile:

```bash
mdrap doctor --json
```

## Expected Output
```json
{
  "status": "HEALTHY",
  "native_c_accelerator": "COMPILED (AVX2/Native)",
  "storage_io": {
    "disk_sync_speed_mbps": 195.9,
    "fsync_latency_ms": 5.1
  }
}
```

## Troubleshooting
- **Error: `MDRAP_API_KEY_SALT must be set`**: The API server refuses to boot without a salt to prevent insecure token hashing. Export `MDRAP_API_KEY_SALT` with at least 16 characters.
- **SQLite Database Locked**: MDRAP configures SQLite WAL mode with a busy timeout. Ensure only one writer process connects to a given SQLite database file.
- **Replication Lag Warning**: Verify TCP connectivity between primary and standby. Standby spools unacknowledged segments and reconnects with exponential backoff.

## Next Steps
- Review all configuration flags in the [Configuration Reference](../reference/config.md).
- Inspect command-line options in the [CLI Reference](../reference/cli.md).
