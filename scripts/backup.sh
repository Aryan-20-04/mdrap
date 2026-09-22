#!/usr/bin/env bash
# ==============================================================================
# MDRAP Online Automated Backup Script (Linux / Cron / Docker)
# ==============================================================================
set -euo pipefail

DB_PATH="${MDRAP_DB_PATH:-data/mdrap.db}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "${BACKUP_DIR}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting MDRAP database backup..."
python3 scripts/backup.py --db "${DB_PATH}" --out-dir "${BACKUP_DIR}" --compress

# Prune old backups older than RETENTION_DAYS
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Pruning backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "mdrap_backup_*.db*" -mtime +"${RETENTION_DAYS}" -delete || true

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Backup and rotation complete."
