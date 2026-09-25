import json
import pytest
from storage import Store, _compute_merkle_root


def test_merkle_root_computation_deterministic():
    leaves = [b"leaf1", b"leaf2", b"leaf3"]
    root1 = _compute_merkle_root(leaves)
    root2 = _compute_merkle_root(leaves)
    assert root1 == root2
    assert len(root1) == 64

    # Different leaves produce different roots
    root3 = _compute_merkle_root([b"leaf1", b"leaf2", b"leaf4"])
    assert root1 != root3


def test_quarantine_merkle_logging_and_verification():
    store = Store(":memory:")

    # Batch 1
    batch1 = [
        ("evt_1", "AAPL", "FEED1", "INVALID", "[]", "{}", 1000.0),
        ("evt_2", "AAPL", "FEED1", "SUSPICIOUS", "[]", "{}", 1001.0),
    ]
    store.write_quarantine_batch(batch1)
    store.commit()

    # Batch 2
    batch2 = [
        ("evt_3", "MSFT", "FEED2", "INVALID", "[]", "{}", 1002.0),
    ]
    store.write_quarantine_batch(batch2)
    store.commit()

    # Verify integrity
    valid, msg, count = store.verify_quarantine_merkle_integrity()
    assert valid is True
    assert count == 2
    assert "verified" in msg.lower()


def test_quarantine_merkle_tamper_detection():
    store = Store(":memory:")

    batch1 = [
        ("evt_1", "AAPL", "FEED1", "INVALID", "[]", "{}", 1000.0),
    ]
    store.write_quarantine_batch(batch1)
    store.commit()

    # Tamper with the batch_root in the database
    store.conn.execute(
        "UPDATE quarantine_merkle_log SET batch_root = 'deadbeef' WHERE entry_id = 1"
    )
    store.commit()

    valid, msg, count = store.verify_quarantine_merkle_integrity()
    assert valid is False
    assert "mismatch" in msg.lower() or "broken" in msg.lower()
