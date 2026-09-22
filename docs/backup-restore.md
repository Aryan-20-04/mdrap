# MDRAP Production Backup & Disaster Recovery Runbook

This guide covers operational procedures for zero-downtime database backups, automated rotation, integrity verification, and atomic disaster recovery in self-hosted environments.

---

## 1. Backup Architecture & Invariants

MDRAP uses SQLite in Write-Ahead Log (WAL) mode for persistence. Taking simple filesystem copies (`cp mdrap.db ...`) while the engine is running can result in corrupt or torn snapshots because active transactions exist across `-wal` and `-shm` sidecar files.

MDRAP provides dedicated backup and restore tools (`scripts/backup.py` and `scripts/restore.py`) that enforce three critical operational invariants:

1. **Zero-Downtime Online Backup**: Uses the SQLite native `Connection.backup()` streaming API with non-blocking WAL checkpointing. The live trading engine continues processing events without interruption.
2. **Page & Cryptographic Integrity Gates**: Automatically executes SQLite `PRAGMA integrity_check` and verifies the tamper-evident SHA-256 Merkle audit chain before declaring a backup successful.
3. **Atomic File Replacement with Safety Snapshots**: When restoring, a safety snapshot of the existing database is saved prior to file replacement, and the swap is executed atomically to eliminate partial writes.

---

## 2. Taking an Online Backup

### Standard CLI Execution
```bash
python scripts/backup.py --db data/mdrap.db --out-dir backups/
```

Output:
```text
[MDRAP Backup] SUCCESS: backups/mdrap_backup_20260922_190000.db (14.25 MB)
  Integrity: PASSED | Audit Chain: PASSED (1420 records)
  Completed in 0.082s
```

### Compressed Backup (Gzip)
```bash
python scripts/backup.py --db data/mdrap.db --out-dir backups/ --compress
```

### JSON Output (For Automation / Monitoring)
```bash
python scripts/backup.py --db data/mdrap.db --compress --json
```

Output:
```json
{
  "status": "SUCCESS",
  "source": "data/mdrap.db",
  "backup_file": "backups/mdrap_backup_20260922_190000.db.gz",
  "size_bytes": 4125890,
  "size_human": "3.93 MB",
  "backup_duration_s": 0.075,
  "total_duration_s": 0.142,
  "integrity_check": "PASSED",
  "audit_chain_status": "PASSED",
  "audit_chain_records": 1420,
  "timestamp_utc": "2026-09-22T19:00:00.123456+00:00"
}
```

---

## 3. Automated Backup Scheduling (Cron / Linux)

Run `scripts/backup.sh` via cron to automate daily backups and prune archives older than 30 days:

```bash
# Open crontab
crontab -e

# Run online backup every hour at minute 0
0 * * * * /app/scripts/backup.sh >> /var/log/mdrap_backup.log 2>&1
```

---

## 4. Disaster Recovery & Database Restoration

### Step 1: Stop the Running Container / Engine
```bash
docker compose stop mdrap-core
# or kill local engine process
```

### Step 2: Execute Verified Restore

```bash
python scripts/restore.py --backup backups/mdrap_backup_20260922_190000.db.gz --db data/mdrap.db --force
```

Output:
```text
[MDRAP Restore] SUCCESS: Restored to data/mdrap.db (14.25 MB)
  Safety snapshot saved to: data/mdrap.db.pre_restore_20260922_190500.bak
  Integrity: PASSED | Audit Chain: PASSED (1420 records)
  Completed in 0.095s
```

### Step 3: Restart Container
```bash
docker compose start mdrap-core
```
Check health:
```bash
curl http://localhost:8000/v1/health
```
