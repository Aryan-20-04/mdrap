import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage import Store
from security import SecurityManager, Role


def test_audit_anchor_detection_of_tail_truncation():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)

        for i in range(5):
            sec.log_audit(f"ACTION_{i}", actor=f"user_{i}", role=Role.VIEWER, details=f"detail_{i}")

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
        valid_with_anchor, err_msg, _ = store.verify_audit_integrity(anchor=(count, head))
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
        assert Store.verify_audit_checkpoint(tampered_cp, secret_key=secret_key) is False

        # Tampered head fails
        tampered_head = dict(cp)
        tampered_head["head"] = "0" * 64
        assert Store.verify_audit_checkpoint(tampered_head, secret_key=secret_key) is False

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
