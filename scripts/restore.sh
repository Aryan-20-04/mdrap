#!/usr/bin/env bash
# ==============================================================================
# MDRAP Automated Restore Script (Linux / Docker)
# Usage: ./scripts/restore.sh <path_to_backup_file> [--force]
# ==============================================================================
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <path_to_backup_file> [--force]"
    exit 1
fi

BACKUP_FILE="$1"
FORCE_FLAG="${2:-}"

DB_PATH="${MDRAP_DB_PATH:-data/mdrap.db}"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Restoring ${BACKUP_FILE} to ${DB_PATH}..."
python3 scripts/restore.py --backup "${BACKUP_FILE}" --db "${DB_PATH}" ${FORCE_FLAG}

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Restore complete and verified."
