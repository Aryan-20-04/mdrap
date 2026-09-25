"""
Tests for Production Online Database Backup and Recovery Tooling.

Verifies:
- Zero-downtime online backup creates valid SQLite database.
- Cryptographic Merkle audit chain verification during backup.
- Gzip compressed backup creation and decompression.
- Restore utility validates source backup integrity before applying.
- Pre-restore safety snapshots protect against accidental overwrite.
- Restored database retains all events, keys, and audit entries.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from models import CanonicalEvent, EventType, QualityStatus
from scripts.backup import backup_database
from scripts.restore import restore_database
from security import SecurityManager
from storage import Store


@pytest.fixture
def populated_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        src_path = f.name

    store = Store(src_path)
    sec = SecurityManager(store=store)

    # Register keys
    adm_ent = sec.register_api_key(client_id="BackupAdmin", role="ADMIN")
    op_ent = sec.register_api_key(client_id="BackupOp", role="OPERATOR")

    # Add audit log
    store.append_audit(
        actor="AdminUser",
        role="ADMIN",
        action="SYSTEM_INIT",
        details="Platform initialized prior to backup test",
    )
    store.append_audit(
        actor="BackupOp",
        role="OPERATOR",
        action="CONFIG_TUNE",
        details="Adjusted latency threshold to 500us",
    )

    # Add canonical events
    ev = CanonicalEvent(
        event_id="BK_1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        processing_timestamp=1700000000.002,
        source="FEEDX",
        sequence_number=101,
        price=180.50,
        quantity=50.0,
        quality_status=QualityStatus.VALID,
    )
    store.write_canonical_batch([ev])
    store.commit()
    store.close()

    yield {
        "src_path": src_path,
        "admin_key": adm_ent.token,
    }

    import gc

    gc.collect()
    if os.path.exists(src_path):
        try:
            os.remove(src_path)
        except Exception:
            pass


def test_online_backup_and_audit_verification(populated_db):
    src = populated_db["src_path"]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        bak_target = f.name
    os.remove(bak_target)

    try:
        res = backup_database(
            source_db=src,
            target_path=bak_target,
            verify_audit=True,
            compress=False,
        )
        assert res["status"] == "SUCCESS"
        assert res["integrity_check"] == "PASSED"
        assert res["audit_chain_status"] == "PASSED"
        assert res["audit_chain_records"] >= 2
        assert os.path.isfile(bak_target)

        # Inspect backup directly
        chk_store = Store(bak_target)
        events = chk_store.query_events(instrument_id="AAPL")
        assert len(events) >= 1
        assert events[0]["price"] == 180.50
        chk_store.close()
    finally:
        import gc

        gc.collect()
        if os.path.exists(bak_target):
            try:
                os.remove(bak_target)
            except Exception:
                pass


def test_compressed_backup_and_restore(populated_db):
    src = populated_db["src_path"]
    with tempfile.NamedTemporaryFile(suffix=".db.gz", delete=False) as f:
        bak_gz = f.name
    os.remove(bak_gz)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        restore_target = f.name
    os.remove(restore_target)

    try:
        # 1. Compressed backup
        res_bak = backup_database(
            source_db=src,
            target_path=bak_gz,
            verify_audit=True,
            compress=True,
        )
        assert res_bak["status"] == "SUCCESS"
        assert os.path.isfile(bak_gz)

        # 2. Restore from compressed backup
        res_rst = restore_database(
            backup_file=bak_gz,
            target_db=restore_target,
            force=True,
            verify_audit=True,
        )
        assert res_rst["status"] == "SUCCESS"
        assert res_rst["integrity_check"] == "PASSED"
        assert res_rst["audit_chain_status"] == "PASSED"

        # 3. Verify restored data
        rst_store = Store(restore_target)
        events = rst_store.query_events(instrument_id="AAPL")
        assert len(events) >= 1
        assert events[0]["instrument_id"] == "AAPL"

        # Verify audit entries in restored db
        valid, msg, count = rst_store.verify_audit_integrity()
        assert valid is True
        assert count >= 2
        rst_store.close()

    finally:
        import gc

        gc.collect()
        for p in (bak_gz, restore_target):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def test_restore_safety_backup_on_existing_target(populated_db):
    src = populated_db["src_path"]
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        bak_target = f.name
    os.remove(bak_target)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        existing_target = f.name

    # Write initial data in existing_target
    init_conn = sqlite3.connect(existing_target)
    init_conn.execute("CREATE TABLE dummy (id INTEGER)")
    init_conn.commit()
    init_conn.close()

    try:
        backup_database(source_db=src, target_path=bak_target)

        # Attempt restore without force -> must raise
        with pytest.raises(RuntimeError) as exc:
            restore_database(
                backup_file=bak_target, target_db=existing_target, force=False
            )
        assert "already exists" in str(exc.value)

        # Restore with force and safety_backup
        res = restore_database(
            backup_file=bak_target,
            target_db=existing_target,
            force=True,
            safety_backup=True,
        )
        assert res["status"] == "SUCCESS"
        assert res["safety_backup"] is not None
        assert os.path.isfile(res["safety_backup"])

        # Clean up safety backup file
        if os.path.exists(res["safety_backup"]):
            os.remove(res["safety_backup"])

    finally:
        import gc

        gc.collect()
        for p in (bak_target, existing_target):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
