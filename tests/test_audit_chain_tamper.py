"""Phase 9 Audit System Cryptographic Chain Hardening Tests.

Tests:
1. Append audit records forming a valid cryptographic SHA-256 hash chain
2. Verification succeeds on an untampered audit chain
3. Corrupting one entry's hash fails verification explicitly
4. Modifying one entry's payload/action fails verification explicitly
5. Deleting an intermediate entry breaks the chain links and fails verification
6. Reordering entries breaks the chain and fails verification
7. Independent audit proof export and verification
"""

import os
import pytest
from storage import Store


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test_audit.db")
    s = Store(path=db_path)
    yield s
    s.close()


def test_audit_append_and_verify(store):
    """Appending multiple audit entries produces an intact, verifiable hash chain."""
    h1 = store.append_audit("admin", "operator", "CONFIG_CHANGE", "threshold=0.05")
    h2 = store.append_audit("admin", "operator", "KEY_ISSUED", "key_id=key_123")
    h3 = store.append_audit("system", "pipeline", "SNAPSHOT_TAKEN", "height=1000")

    assert h1 != h2 != h3
    ok, msg, _ = store.verify_audit_integrity()
    assert ok is True
    assert "Cryptographic audit chain verified (3 entries intact)" in msg


def test_audit_tamper_corrupt_entry_hash(store):
    """Corrupting a stored hash directly in the database causes verification failure."""
    store.append_audit("admin", "operator", "A1", "d1")
    store.append_audit("admin", "operator", "A2", "d2")
    store.append_audit("admin", "operator", "A3", "d3")

    # Tamper with row 2 entry_hash
    with store._lock:
        store.conn.execute(
            "UPDATE audit_log SET entry_hash = 'tampered_hash_value' WHERE entry_id = 2"
        )
        store.conn.commit()

    ok, msg, _ = store.verify_audit_integrity()
    assert ok is False
    assert "hash mismatch" in msg.lower() and "entry #2" in msg


def test_audit_tamper_modify_payload(store):
    """Modifying action or details under the original hash causes recalculation mismatch."""
    store.append_audit("admin", "operator", "A1", "d1")
    store.append_audit("admin", "operator", "A2", "d2")

    # Silently modify details without updating hash
    with store._lock:
        store.conn.execute(
            "UPDATE audit_log SET details = 'malicious_modification' WHERE entry_id = 1"
        )
        store.conn.commit()

    ok, msg, _ = store.verify_audit_integrity()
    assert ok is False
    assert "hash mismatch" in msg.lower() and "entry #1" in msg


def test_audit_tamper_delete_entry(store):
    """Deleting an intermediate entry breaks the prev_hash link."""
    store.append_audit("admin", "operator", "A1", "d1")
    store.append_audit("admin", "operator", "A2", "d2")
    store.append_audit("admin", "operator", "A3", "d3")

    with store._lock:
        store.conn.execute("DELETE FROM audit_log WHERE entry_id = 2")
        store.conn.commit()

    ok, msg, _ = store.verify_audit_integrity()
    assert ok is False
    assert "broken chain link" in msg.lower() and "entry #3" in msg


def test_audit_tamper_reorder_entries(store):
    """Reordering entries causes sequence/link mismatch."""
    store.append_audit("admin", "operator", "A1", "d1")
    store.append_audit("admin", "operator", "A2", "d2")

    with store._lock:
        store.conn.execute("UPDATE audit_log SET entry_id = 99 WHERE entry_id = 1")
        store.conn.execute("UPDATE audit_log SET entry_id = 1 WHERE entry_id = 2")
        store.conn.execute("UPDATE audit_log SET entry_id = 2 WHERE entry_id = 99")
        store.conn.commit()

    ok, msg, _ = store.verify_audit_integrity()
    assert ok is False
    assert not ok


def test_audit_independent_proof_export_and_verify(store, tmp_path):
    """Exporting audit proof to JSON and verifying offline detects tampering."""
    store.append_audit("admin", "operator", "START", "init")
    store.append_audit("admin", "operator", "STOP", "halt")

    proof_file = str(tmp_path / "proof.json")
    proof = store.export_audit_proof(proof_file)
    assert os.path.exists(proof_file)
    assert proof["total_entries"] == 2

    # Verification passes
    ok, msg, _ = Store.verify_standalone_proof(proof_file)
    assert ok is True

    # Tamper with offline proof file
    import json

    with open(proof_file, "r") as f:
        data = json.load(f)
    data["entries"][0]["details"] = "hacked"
    with open(proof_file, "w") as f:
        json.dump(data, f)

    ok_tampered, msg_tampered, _ = Store.verify_standalone_proof(proof_file)
    assert ok_tampered is False
    assert (
        "tampering" in msg_tampered.lower()
        or "mismatch" in msg_tampered.lower()
        or "tampered" in msg_tampered.lower()
    )
