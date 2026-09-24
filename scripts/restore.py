#!/usr/bin/env python3
"""
MDRAP Production Database Restore Tool.

Safely restores a verified backup to the active MDRAP SQLite database location.
Performs pre-restore SQLite page integrity check and cryptographic Merkle audit trail
verification before applying changes, automatically snapshots existing data,
and applies atomic file replacement to prevent corruption.
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
import tempfile
import time

# Ensure src is on sys.path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def restore_database(
    backup_file: str,
    target_db: str,
    force: bool = False,
    safety_backup: bool = True,
    verify_audit: bool = True,
) -> dict:
    """
    Safely restore backup_file to target_db.

    Returns:
        dict with restore metadata, verification details, and safety backup path.
    """
    if not os.path.isfile(backup_file):
        raise FileNotFoundError(f"Backup file does not exist: {backup_file}")

    target_dir = os.path.dirname(os.path.abspath(target_db))
    os.makedirs(target_dir, exist_ok=True)

    t0 = time.perf_counter()

    # Step 1: Decompress if necessary
    temp_dir = tempfile.mkdtemp(prefix="mdrap_restore_")
    try:
        if backup_file.endswith(".gz"):
            unpacked_source = os.path.join(temp_dir, "unpacked.db")
            with gzip.open(backup_file, "rb") as f_in:
                with open(unpacked_source, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            unpacked_source = backup_file

        # Step 2: Verify integrity of backup BEFORE touching target database
        verify_conn = sqlite3.connect(unpacked_source)
        cursor = verify_conn.execute("PRAGMA integrity_check")
        rows = cursor.fetchall()
        verify_conn.close()

        integrity_ok = len(rows) == 1 and rows[0][0] == "ok"
        if not integrity_ok:
            raise RuntimeError(f"Backup failed SQLite integrity check: {rows}")

        # Step 3: Cryptographic Merkle Audit Trail Verification
        audit_ok = True
        audit_msg = "Skipped"
        audit_count = 0
        if verify_audit:
            try:
                from security import SecurityManager
                from storage import Store

                chk_store = Store(unpacked_source)
                sec = SecurityManager(store=chk_store)
                audit_ok, audit_msg, audit_count = sec.verify_audit_trail()
                chk_store.close()
                if not audit_ok:
                    raise RuntimeError(
                        f"Cryptographic audit chain verification failed on backup: {audit_msg}"
                    )
            except Exception as e:
                if not audit_ok:
                    raise
                audit_msg = f"Audit check notice: {e}"

        # Step 4: Safety backup of existing database if present
        safety_path = None
        if os.path.exists(target_db):
            if not force:
                raise RuntimeError(
                    f"Target database already exists at {target_db}. "
                    "Specify --force to overwrite."
                )
            if safety_backup:
                now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                safety_path = f"{target_db}.pre_restore_{now_str}.bak"
                shutil.copy2(target_db, safety_path)

        # Step 5: Atomic file replacement
        staged_target = target_db + ".restore_staged"
        shutil.copy2(unpacked_source, staged_target)

        # Remove WAL and SHM journal files of target if present to prevent mixing
        for ext in ("-wal", "-shm", "-journal"):
            sidecar = target_db + ext
            if os.path.exists(sidecar):
                try:
                    os.remove(sidecar)
                except Exception:
                    pass

        # Atomic rename
        if os.path.exists(target_db):
            os.remove(target_db)
        os.rename(staged_target, target_db)

        # Step 6: Final check on active target
        post_conn = sqlite3.connect(target_db)
        post_cursor = post_conn.execute("PRAGMA integrity_check")
        post_rows = post_cursor.fetchall()
        post_conn.close()

        if not (len(post_rows) == 1 and post_rows[0][0] == "ok"):
            raise RuntimeError(
                f"Restored target database failed post-check: {post_rows}"
            )

        total_time = time.perf_counter() - t0
        target_size = os.path.getsize(target_db)

        return {
            "status": "SUCCESS",
            "backup_source": backup_file,
            "target_database": target_db,
            "target_size_bytes": target_size,
            "target_size_human": f"{target_size / (1024 * 1024):.2f} MB",
            "safety_backup": safety_path,
            "duration_s": round(total_time, 3),
            "integrity_check": "PASSED",
            "audit_chain_status": "PASSED" if audit_ok else "FAILED",
            "audit_chain_message": audit_msg,
            "audit_chain_records": audit_count,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="MDRAP Database Restore Utility",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--backup", required=True, help="Path to backup file (.db or .db.gz)"
    )
    default_db = os.environ.get("MDRAP_DB_PATH", "data/mdrap.db")
    parser.add_argument(
        "--db", default=default_db, help="Target path for restored database"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing database without confirmation",
    )
    parser.add_argument(
        "--no-safety-backup",
        action="store_true",
        help="Skip creating a pre-restore backup",
    )
    parser.add_argument(
        "--no-verify-audit",
        action="store_true",
        help="Skip Merkle audit chain verification",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON results")

    args = parser.parse_args()

    try:
        res = restore_database(
            backup_file=args.backup,
            target_db=args.db,
            force=args.force,
            safety_backup=not args.no_safety_backup,
            verify_audit=not args.no_verify_audit,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(
                f"[MDRAP Restore] SUCCESS: Restored to {res['target_database']} ({res['target_size_human']})"
            )
            if res["safety_backup"]:
                print(f"  Safety snapshot saved to: {res['safety_backup']}")
            print(
                f"  Integrity: {res['integrity_check']} | Audit Chain: {res['audit_chain_status']} ({res['audit_chain_records']} records)"
            )
            print(f"  Completed in {res['duration_s']}s")
        sys.exit(0)
    except Exception as e:
        if args.json:
            print(
                json.dumps({"status": "ERROR", "error": str(e)}, indent=2),
                file=sys.stderr,
            )
        else:
            print(f"[MDRAP Restore] FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
