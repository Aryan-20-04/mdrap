# MDRAP Operational Troubleshooting & Diagnostics Guide

This document assists operations teams and administrators in diagnosing and resolving common operational issues.

---

## 1. Fast Health & Diagnostic Check

Start by inspecting platform health and watchdog state:

```bash
# Via CLI:
python cli.py status

# Via HTTP API:
curl http://localhost:8000/v1/health
```

---

## 2. Common Scenarios & Resolutions

### Issue 1: `ConnectionRefusedError: [WinError 10061]` or Port 8000 Not Reaching Server
- **Cause**: MDRAP server is not running or bound strictly to `127.0.0.1` while being queried from an external container or host IP.
- **Resolution**:
  - Verify container status: `docker compose ps`
  - Check container logs: `docker compose logs -f mdrap-core`
  - Ensure server is bound to `0.0.0.0`: `python cli.py serve --host 0.0.0.0 --port 8000`

### Issue 2: `401 Unauthorized` on API Requests
- **Cause**: Missing `X-API-Key` or `Authorization: Bearer` header, or token has been revoked.
- **Resolution**:
  - List registered keys via CLI:
    ```bash
    python cli.py keys list
    ```
  - Generate a fresh key if lost:
    ```bash
    python cli.py keys create --client-id NewDesk --role OPERATOR
    ```

### Issue 3: `403 Forbidden` on Specific Endpoints
- **Cause**: Role boundary violation (e.g. attempting to view quarantine with a `VIEWER` key, or manage keys with an `OPERATOR` key).
- **Resolution**:
  - Verify required role in [API Reference](api.md).
  - Regenerate or reassign an API key with appropriate role (`OPERATOR` for quarantine and audit; `ADMIN` for key and feed management).

### Issue 4: Source Watchdog Reports `SILENT` or `DEGRADED` Feed
- **Cause**: Upstream feed provider stopped sending packets or heartbeat delay exceeded `silence_threshold_s` (default 3.0s).
- **Resolution**:
  - Check watchdog status:
    ```bash
    python cli.py watchdog
    ```
  - Verify network connectivity to upstream market data vendor:
    ```bash
    curl -I https://api.polygon.io
    ```
  - If a feed was blocked manually or permanently degraded:
    ```bash
    # Unblock source via CLI
    python cli.py watchdog --unblock <SOURCE_NAME>
    ```

### Issue 5: SQLite Database Locked / Busy
- **Cause**: Concurrent uncoordinated writes or external processes holding long read locks without WAL mode.
- **Resolution**:
  - Verify WAL mode is active:
    ```sql
    PRAGMA journal_mode;
    ```
    Should return `wal`.
  - MDRAP automatically configures `PRAGMA busy_timeout = 30000` (30 seconds) and WAL mode on initialization.
  - Avoid opening the database file in interactive external GUI tools with exclusive lock modes while the platform is running.
  - Use online backup tool `scripts/backup.py` instead of raw filesystem copies.

### Issue 6: Merkle Audit Chain Verification Failure
- **Cause**: Out-of-band edit or deletion of rows in the `audit_log` SQLite table.
- **Resolution**:
  - Run verification via CLI:
    ```bash
    python cli.py audit --verify
    ```
  - The CLI outputs the exact entry ID where the hash linkage broke:
    `Broken chain link at entry #42: expected prev 'abc123...', got 'xyz789...'`
  - Restore the database from the most recent verified backup:
    ```bash
    python scripts/restore.py --backup backups/mdrap_backup_latest.db --db data/mdrap.db --force
    ```

### Issue 7: SHM Ring Buffer Watermark Alert / Consumer Lag
- **Cause**: Shared memory reader is consuming ticks slower than the publisher rate; buffer occupancy crossed the 80% watermark threshold.
- **Resolution**:
  - Check reader processing loop: ensure no I/O, disk writes, or blocking calls occur inside the hot streaming loop.
  - Pin the reader process to an isolated CPU core.
  - Increase ring buffer capacity by configuring a larger power-of-two `slot_count` (e.g. 65,536 slots).
  - Tune watermark warning threshold via `MDRAP_SHM_WATERMARK_PCT` (default `0.80`).

### Issue 8: High Out-of-Order / Sequence-Gap Quarantines Due to Network Jitter
- **Cause**: Microsecond UDP packet arrival jitter or multi-queue NIC race conditions delivering packets slightly out of sequence.
- **Resolution**:
  - Configure the in-flight reorder buffer in `config.yaml` or via CLI:
    ```yaml
    quality:
      reorder_window_s: 0.005   # 5ms sliding delay window
      reorder_max_slots: 32     # hold up to 32 slots in flight
    ```
  - Contiguous inverted packets will self-repair in-memory and be marked `VALID` instead of being prematurely quarantined.

