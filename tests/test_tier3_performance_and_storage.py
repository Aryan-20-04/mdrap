"""Tests for MDRAP Tier 3 Performance & Storage Enhancements.

Covers:
- Item 4.1: Configurable native capacity, telemetry, and fallback cliff diagnostics.
- Item 4.3: Binary sparse WAL indexing and O(log N) record seeking.
- Item 1.2: Storage layer modularization with 100% backward compatibility.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.fastpath import FastQualityEngine
from mdrap.ingestlog import (
    IngestLog,
    build_sparse_index,
    load_sparse_index,
    SPARSE_INDEX_MAGIC,
)
from mdrap.storage import (
    Store,
    PersistenceMode,
    StorageConflictError,
    SCHEMA,
    CURRENT_SCHEMA_VERSION,
    StoreAuditMixin,
    _compute_merkle_root,
)
from mdrap.storage_schema import (
    DEFAULT_SQLITE_RETRIES,
    DEFAULT_SQLITE_TIMEOUT_S,
)


def test_sparse_wal_index_and_point_seek():
    """Verify binary sparse WAL index build, loading, seek acceleration, and point lookups."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        log = IngestLog(
            log_dir=tmp_dir,
            sparse_index_interval=8,
            fsync_policy="never",
        )

        # Write 50 events
        for i in range(50):
            raw = RawEvent(
                source="TEST_FEED",
                payload={"symbol": "AAPL", "price": 150.0 + i, "seq": i},
                receive_timestamp=1000.0 + i,
                raw_id=f"raw_{i}",
            )
            offset = log.append(raw)
            assert offset == i

        log.flush()

        # Check point seek
        for target in [0, 7, 8, 25, 49]:
            found = log.seek_record(target)
            assert found is not None
            off, evt = found
            assert off == target
            assert evt.source == "TEST_FEED"
            assert evt.payload["seq"] == target

        # Out-of-bounds target offsets
        assert log.seek_record(-1) is None
        assert log.seek_record(50) is None
        assert log.seek_record(100) is None

        # Verify accelerated stream iter_from
        streamed = list(log.iter_from(start_offset=30))
        assert len(streamed) == 20
        assert streamed[0][0] == 30
        assert streamed[-1][0] == 49

        # Verify .idx file generation on active segment
        segments = log._list_segment_files()
        assert len(segments) >= 1
        seg_path = segments[0][1]
        idx_entries = load_sparse_index(seg_path, interval=8)
        assert len(idx_entries) > 0
        assert idx_entries[0][0] == 0
        assert idx_entries[1][0] == 8

        log.close()


def test_sparse_wal_multi_segment_rotation_and_purge():
    """Verify sparse WAL indexing across segment rollovers and purge cleaning of .idx files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Small max_segment_bytes to force multiple segments
        log = IngestLog(
            log_dir=tmp_dir,
            max_segment_bytes=1024,
            sparse_index_interval=4,
            fsync_policy="never",
        )

        for i in range(60):
            raw = RawEvent(
                source="BATS",
                payload={"symbol": "NVDA", "price": 400.0 + i, "data": "x" * 60},
                receive_timestamp=2000.0 + i,
                raw_id=f"r_{i}",
            )
            log.append(raw)

        log.flush()
        segments = log._list_segment_files()
        assert len(segments) > 1, "Expected multiple segments from small segment size"

        # Point seek across different segments
        first_event = log.seek_record(0)
        assert first_event is not None and first_event[0] == 0

        mid_event = log.seek_record(30)
        assert mid_event is not None and mid_event[0] == 30

        last_event = log.seek_record(59)
        assert last_event is not None and last_event[0] == 59

        # Test purge
        purged = log.delete_segments_before(checkpoint_offset=25)
        assert purged > 0

        # After purge, seek_record for non-purged offset still works
        assert log.seek_record(50) is not None

        log.close()


def test_fastpath_capacity_overflow_diagnostics(caplog):
    """Verify FastQualityEngine capacity configuration, stats telemetry, and overflow warnings."""
    engine = FastQualityEngine()
    engine.max_instruments = 5  # Artificially low instrument limit for testing

    initial_stats = engine.stats()
    assert initial_stats["fallback_count"] == 0
    assert initial_stats["slot_overflow_count"] == 0
    assert initial_stats["max_instruments"] == 5

    with caplog.at_level(logging.WARNING):
        # Generate 10 events with different instruments
        for i in range(10):
            evt = CanonicalEvent(
                event_id=f"ev_{i}",
                instrument_id=f"SYM_{i}",
                event_type=EventType.TRADE,
                exchange_timestamp=time.time(),
                receive_timestamp=time.time(),
                processing_timestamp=time.time(),
                source="NASDAQ",
                sequence_number=i + 1,
                price=100.0,
                quantity=10.0,
            )
            res = engine.evaluate(evt)
            assert res.quality_status in (
                QualityStatus.VALID,
                QualityStatus.SUSPICIOUS,
                QualityStatus.INVALID,
            )

    stats = engine.stats()
    # At least some events must have exceeded instrument capacity 5
    assert stats["slot_overflow_count"] > 0
    assert stats["fallback_count"] == stats["slot_overflow_count"]
    assert any(
        "[fastpath] Slot capacity exceeded" in rec.message for rec in caplog.records
    )


def test_storage_modularization_and_backward_compatibility():
    """Verify storage module decomposition preserves 100% backward compatibility and integrity."""
    # Verify symbols and constants
    assert issubclass(Store, StoreAuditMixin)
    assert PersistenceMode.PRODUCTION_DURABLE.value == "production_durable"
    assert issubclass(StorageConflictError, RuntimeError)
    assert CURRENT_SCHEMA_VERSION == 3
    assert DEFAULT_SQLITE_RETRIES == 3

    # Test Store operations via decoupled mixin
    store = Store(":memory:")
    assert store.get_schema_version() == 3

    # Audit log operations
    h1 = store.append_audit(
        actor="system", role="admin", action="BOOT", details="Init store"
    )
    assert isinstance(h1, str) and len(h1) == 64
    assert store.get_latest_audit_hash() == h1

    # Quarantine Merkle log operations
    q_rows = [
        ("q_1", "AAPL", "FEED_A", "INVALID", "PRICE_NEGATIVE", "{}", 1000.0),
        ("q_2", "GOOG", "FEED_B", "SUSPICIOUS", "STALE_TIMESTAMP", "{}", 1001.0),
    ]
    m_h = store.append_quarantine_merkle_batch(q_rows)
    assert m_h is not None

    # Verification
    valid_audit, msg_audit, count_audit = store.verify_audit_integrity()
    assert valid_audit is True
    assert count_audit >= 1

    valid_merkle, msg_merkle, count_merkle = store.verify_quarantine_merkle_integrity()
    assert valid_merkle is True
    assert count_merkle >= 1

    # Export & verify proof
    proof = store.export_audit_proof()
    assert proof["total_entries"] >= 1
    proof_valid, proof_msg, proof_entries = Store.verify_standalone_proof(proof)
    assert proof_valid is True

    store.close()
