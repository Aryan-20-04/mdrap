import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.storage import Store
from mdrap.security import SecurityManager, Role


def test_audit_anchor_detection_of_tail_truncation():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)

        for i in range(5):
            sec.log_audit(
                f"ACTION_{i}",
                actor=f"user_{i}",
                role=Role.VIEWER,
                details=f"detail_{i}",
            )

        count, head = store.get_audit_anchor()
        assert count == 5
        assert len(head) == 64

        # Valid with correct anchor
        valid, msg, c = store.verify_audit_integrity(anchor=(count, head))
        assert valid is True
        assert c == 5

        # Simulating truncation: delete the 5th entry directly in DB
        store.conn.execute("DELETE FROM audit_log WHERE entry_id = 5")
        store.commit()

        # Without anchor, remaining 4 entries still have unbroken internal links!
        valid_no_anchor, _, c_no = store.verify_audit_integrity(anchor=None)
        assert valid_no_anchor is True
        assert c_no == 4

        # WITH anchor: Caught! Tail truncation detected!
        valid_with_anchor, err_msg, _ = store.verify_audit_integrity(
            anchor=(count, head)
        )
        assert valid_with_anchor is False
        assert "Tail truncation detected" in err_msg or "Chain head mismatch" in err_msg

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_audit_checkpoint_hmac_signing_and_verification():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)

        sec.log_audit("BOOT", actor="system", role=Role.ADMIN, details="Engine boot")
        sec.log_audit("CONFIG", actor="admin", role=Role.ADMIN, details="Loaded limits")

        secret_key = "super_secure_audit_hmac_secret"
        cp = store.sign_audit_checkpoint(secret_key=secret_key)
        assert cp["count"] == 2
        assert "head" in cp
        assert "signature" in cp

        # Verify correct checkpoint
        assert Store.verify_audit_checkpoint(cp, secret_key=secret_key) is True

        # Wrong secret key fails
        assert Store.verify_audit_checkpoint(cp, secret_key="wrong_secret") is False

        # Tampered count fails
        tampered_cp = dict(cp)
        tampered_cp["count"] = 3
        assert (
            Store.verify_audit_checkpoint(tampered_cp, secret_key=secret_key) is False
        )

        # Tampered head fails
        tampered_head = dict(cp)
        tampered_head["head"] = "0" * 64
        assert (
            Store.verify_audit_checkpoint(tampered_head, secret_key=secret_key) is False
        )

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_audit_anchor_accepts_valid_append_after_checkpoint():
    """An anchor authenticates a chain prefix and remains valid after honest growth."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)
        sec.log_audit("BOOT", actor="system", role=Role.ADMIN, details="start")
        checkpoint = store.get_audit_anchor()

        sec.log_audit("CONFIG", actor="admin", role=Role.ADMIN, details="updated")
        valid, message, count = store.verify_audit_integrity(anchor=checkpoint)

        assert valid is True, message
        assert count == 2
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_audit_checkpoint_detects_prefix_rewrite_and_chain_splice():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)
        sec.log_audit("FIRST", actor="operator", role=Role.OPERATOR, details="one")
        sec.log_audit("SECOND", actor="operator", role=Role.OPERATOR, details="two")
        checkpoint = store.get_audit_anchor()

        store.conn.execute(
            "UPDATE audit_log SET details = ? WHERE entry_id = 1", ("rewritten",)
        )
        store.commit()
        valid, _, _ = store.verify_audit_integrity(anchor=checkpoint)
        assert valid is False

        store.conn.execute(
            "UPDATE audit_log SET details = ? WHERE entry_id = 1", ("one",)
        )
        store.conn.execute(
            "UPDATE audit_log SET prev_hash = ? WHERE entry_id = 2", ("f" * 64,)
        )
        store.commit()
        valid, message, _ = store.verify_audit_integrity()
        assert valid is False
        assert "Broken chain link" in message
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_unanchored_fully_rewritten_chain_is_not_detectable():
    """Document the verifier limit: unkeyed hashes cannot authenticate their own history."""
    from mdrap.audit_format import compute_audit_hash

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)
        sec.log_audit("ORIGINAL", actor="operator", role=Role.OPERATOR, details="one")
        checkpoint = store.get_audit_anchor()
        rows = store.conn.execute(
            "SELECT entry_id,timestamp,actor,role,action,details,format_version "
            "FROM audit_log ORDER BY entry_id"
        ).fetchall()
        prev = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        for row in rows:
            entry_id, ts, actor, role, action, _details, version = row
            forged_details = "rewritten by database writer"
            forged_hash = compute_audit_hash(
                prev, ts, actor, role, action, forged_details, format_version=version
            )
            store.conn.execute(
                "UPDATE audit_log SET details=?,prev_hash=?,entry_hash=? WHERE entry_id=?",
                (forged_details, prev, forged_hash, entry_id),
            )
            prev = forged_hash
        store.commit()

        valid_without_anchor, _, _ = store.verify_audit_integrity()
        valid_with_anchor, _, _ = store.verify_audit_integrity(anchor=checkpoint)
        assert valid_without_anchor is True
        assert valid_with_anchor is False
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
