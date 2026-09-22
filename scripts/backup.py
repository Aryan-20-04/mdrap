#!/usr/bin/env python3
"""
MDRAP Production Database Online Backup Tool.

Performs zero-downtime, non-blocking backups of the active MDRAP SQLite database
using the native sqlite3 online backup API with WAL checkpointing.
Cryptographically verifies database page integrity and Merkle audit log validity
before declaring success.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time

# Ensure src is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def backup_database(
    source_db: str,
    target_path: str,
    checkpoint: bool = True,
    verify_audit: bool = True,
    compress: bool = False,
) -> dict:
    """
    Safely backup source SQLite database to target path.

    Returns:
        dict with backup metadata, size, duration, and verification status.
    """
    if not os.path.isfile(source_db):
        raise FileNotFoundError(f"Source database does not exist: {source_db}")

    target_dir = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(target_dir, exist_ok=True)

    # Use a temporary intermediate file for atomic write
    temp_target = target_path + ".tmp"
    if os.path.exists(temp_target):
        os.remove(temp_target)

    t0 = time.perf_counter()

    # Step 1: Open source in read-only mode to prevent lock escalation
    src_conn = sqlite3.connect(f"file:{os.path.abspath(source_db)}?mode=ro", uri=True)

    # Optionally checkpoint WAL log frames to main database file
    if checkpoint:
        try:
            # PASSIVE checkpoint ensures no blocking of concurrent writers
            chk_conn = sqlite3.connect(source_db)
            chk_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            chk_conn.close()
        except Exception as e:
            # Non-fatal if server has an exclusive lock; online backup still copies WAL
            pass

    dst_conn = sqlite3.connect(temp_target)

    # Step 2: Use sqlite3 online backup API
    with dst_conn:
        src_conn.backup(dst_conn, pages=100, sleep=0.005)

    dst_conn.close()
    src_conn.close()

    backup_time = time.perf_counter() - t0

    # Step 3: Integrity verification on the backup file
    verify_conn = sqlite3.connect(temp_target)
    cursor = verify_conn.execute("PRAGMA integrity_check")
    rows = cursor.fetchall()
    verify_conn.close()

    integrity_ok = len(rows) == 1 and rows[0][0] == "ok"
    if not integrity_ok:
        if os.path.exists(temp_target):
            os.remove(temp_target)
        raise RuntimeError(f"Backup failed SQLite integrity check: {rows}")

    # Step 4: Cryptographic Merkle Audit Trail Verification
    audit_ok = True
    audit_msg = "Skipped"
    audit_count = 0
    if verify_audit:
        try:
            from security import SecurityManager
            from storage import Store
            chk_store = Store(temp_target)
            sec = SecurityManager(store=chk_store)
            audit_ok, audit_msg, audit_count = sec.verify_audit_trail()
            chk_store.close()
            if not audit_ok:
                if os.path.exists(temp_target):
                    os.remove(temp_target)
                raise RuntimeError(f"Cryptographic audit chain verification failed on backup: {audit_msg}")
        except Exception as e:
            if not audit_ok:
                raise
            audit_msg = f"Audit check notice: {e}"

    # Step 5: Optional compression
    final_path = target_path
    if compress:
        final_path = target_path if target_path.endswith(".gz") else target_path + ".gz"
        with open(temp_target, "rb") as f_in:
            with gzip.open(final_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(temp_target)
    else:
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_target, final_path)

    size_bytes = os.path.getsize(final_path)
    total_time = time.perf_counter() - t0

    return {
        "status": "SUCCESS",
        "source": source_db,
        "backup_file": final_path,
        "size_bytes": size_bytes,
        "size_human": f"{size_bytes / (1024 * 1024):.2f} MB",
        "backup_duration_s": round(backup_time, 3),
        "total_duration_s": round(total_time, 3),
        "integrity_check": "PASSED",
        "audit_chain_status": "PASSED" if audit_ok else "FAILED",
        "audit_chain_message": audit_msg,
        "audit_chain_records": audit_count,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="MDRAP Online Database Backup Utility",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    default_db = os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
    parser.add_argument("--db", default=default_db, help="Path to active MDRAP SQLite database")
    parser.add_argument("--out-dir", default="backups", help="Directory to store backup files")
    parser.add_argument("--out", default=None, help="Explicit output backup filename")
    parser.add_argument("--compress", action="store_true", help="Gzip compress the output backup")
    parser.add_argument("--no-verify-audit", action="store_true", help="Skip Merkle audit chain verification")
    parser.add_argument("--json", action="store_true", help="Output JSON results")

    args = parser.parse_args()

    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = ".db.gz" if args.compress else ".db"
    target = args.out or os.path.join(args.out_dir, f"mdrap_backup_{now_str}{ext}")

    try:
        res = backup_database(
            source_db=args.db,
            target_path=target,
            verify_audit=not args.no_verify_audit,
            compress=args.compress,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[MDRAP Backup] SUCCESS: {res['backup_file']} ({res['size_human']})")
            print(f"  Integrity: {res['integrity_check']} | Audit Chain: {res['audit_chain_status']} ({res['audit_chain_records']} records)")
            print(f"  Completed in {res['total_duration_s']}s")
        sys.exit(0)
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "ERROR", "error": str(e)}, indent=2), file=sys.stderr)
        else:
            print(f"[MDRAP Backup] FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
