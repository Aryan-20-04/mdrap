"""
Phase 11: Storage, Durability, and Concurrency Tests.
Validates:
- STOR-01: Binary journal 128-tick overwrite prevention and uncommitted slot scan-forward recovery.
- STOR-02: Zero silent event drops in SHMDrainWorker when store operations raise exceptions.
- STOR-03: Forced fsync durability on BinaryJournal and RawArchive flush pathways.
- CONC-03: Shared lock unification for in-memory SQLite connections and busy retry in transaction().
"""

import os
import struct
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from mdrap.journal import BinaryJournal, BinaryJournalReader, JOURNAL_HDR_STRUCT
from mdrap.archive import RawArchive
from mdrap.shm_drainer import SHMDrainWorker
from mdrap.storage import Store
from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus


def test_stor_01_journal_partial_batch_reopen_no_overwrite():
    with tempfile.TemporaryDirectory() as tmpdir:
        jpath = os.path.join(tmpdir, "test.dbn")

        # 1. Write 42 records (well under the old 128-record flush interval)
        j1 = BinaryJournal(jpath, initial_records=1024)
        for i in range(1, 43):
            j1.append_tick(
                seq=i,
                symbol="AAPL",
                source="NASDAQ",
                price=150.0 + i,
                size=100.0,
                status="VALID",
            )
        assert j1.record_count == 42
        assert j1.last_seq == 42
        j1.close(truncate_to_used=False)

        # 2. Reopen existing journal
        j2 = BinaryJournal(jpath)
        assert j2.record_count == 42
        assert j2.first_seq == 1
        assert j2.last_seq == 42

        # 3. Append another 30 records
        for i in range(43, 73):
            j2.append_tick(
                seq=i,
                symbol="AAPL",
                source="NASDAQ",
                price=150.0 + i,
                size=100.0,
                status="VALID",
            )
        assert j2.record_count == 72
        assert j2.last_seq == 72
        j2.close(truncate_to_used=True)

        # 4. Read back all records and verify NO overwrite occurred
        with BinaryJournalReader(jpath) as reader:
            assert reader.record_count == 72
            records = list(reader)
            assert len(records) == 72
            for idx, rec in enumerate(records, start=1):
                assert rec["seq"] == idx
                assert abs(rec["price"] - (150.0 + idx)) < 1e-4


def test_stor_01_journal_scan_forward_crash_recovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        jpath = os.path.join(tmpdir, "crash_test.dbn")

        # Write 25 records
        j = BinaryJournal(jpath, initial_records=512)
        for i in range(1, 26):
            j.append_tick(
                seq=i,
                symbol="MSFT",
                source="BATS",
                price=300.0 + i,
                size=50.0,
            )
        j.close(truncate_to_used=False)

        # Deliberately corrupt the header record_count back to 0 (simulating crash before header sync)
        with open(jpath, "r+b") as f:
            hdr_bytes = f.read(128)
            magic, ver, rec_sz, epoch, count, first_seq, last_seq, c_ts, u_ts, pad = (
                JOURNAL_HDR_STRUCT.unpack(hdr_bytes)
            )
            # Overwrite count, first_seq, last_seq with 0
            tampered_hdr = JOURNAL_HDR_STRUCT.pack(
                magic, ver, rec_sz, epoch, 0, 0, 0, c_ts, u_ts, pad
            )
            f.seek(0)
            f.write(tampered_hdr)

        # Reopen journal: scan-forward logic must detect the 25 written slots and recover!
        j_recovered = BinaryJournal(jpath)
        assert j_recovered.record_count == 25
        assert j_recovered.first_seq == 1
        assert j_recovered.last_seq == 25

        # Further appends must resume at slot 26, not 0
        j_recovered.append_tick(
            seq=26, symbol="MSFT", source="BATS", price=326.0, size=50.0
        )
        assert j_recovered.record_count == 26
        j_recovered.close(truncate_to_used=True)

        with BinaryJournalReader(jpath) as reader:
            recs = list(reader)
            assert len(recs) == 26
            assert recs[0]["seq"] == 1
            assert recs[-1]["seq"] == 26


def test_stor_02_shm_drainer_retains_batch_on_store_exception():
    with patch("shm_drainer.SHMReader"):
        worker = SHMDrainWorker(shm_name="dummy_shm", batch_size=10)
        mock_store = MagicMock()
        mock_store.write_batches_atomic.side_effect = RuntimeError(
            "SQLite database is locked"
        )
        worker.store = mock_store

        event1 = CanonicalEvent(
            event_id="e1",
            instrument_id="SPY",
            event_type=EventType.TRADE,
            exchange_timestamp=100.0,
            receive_timestamp=100.1,
            processing_timestamp=100.2,
            source="FEED",
            sequence_number=1,
            price=450.0,
            quantity=100.0,
            quality_status=QualityStatus.VALID,
            reasons=[],
            raw_id="raw_1",
        )
        worker._pending_canonical = [event1]

        # Flush batch: Store fails, events must NOT be lost
        worker._flush_batches()
        assert len(worker._pending_canonical) == 1
        assert worker._pending_canonical[0].event_id == "e1"
        assert worker.stats.store_errors == 1

        # Now simulate store recovery
        mock_store.write_batches_atomic.side_effect = None
        worker._flush_batches()
        assert len(worker._pending_canonical) == 0
        assert mock_store.write_batches_atomic.call_count == 2


def test_stor_03_fsync_durability_invocations():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Test BinaryJournal fsync on flush
        jpath = os.path.join(tmpdir, "fsync_journal.dbn")
        journal = BinaryJournal(jpath, initial_records=128)
        with patch("os.fsync") as mock_fsync:
            journal.append_tick(
                seq=1, symbol="NVDA", source="NASDAQ", price=120.0, size=10.0
            )
            journal.flush()
            assert mock_fsync.called
        journal.close()

        # 2. Test RawArchive fsync on flush
        archive_dir = os.path.join(tmpdir, "archive")
        archive = RawArchive(base_dir=archive_dir, buffer_size=100)
        raw = RawEvent(
            raw_id="r1",
            source="BINANCE",
            payload={"p": 1},
            receive_timestamp=time.time(),
        )
        with patch("os.fsync") as mock_fsync:
            archive.write(raw)
            archive.flush()
            assert mock_fsync.called
        archive.close()


def test_conc_03_in_memory_store_lock_unification():
    # In-memory store must share the same lock instance for read and write
    store = Store(":memory:")
    try:
        assert store._read_lock is store._lock

        # Concurrently perform writes and reads across 6 threads
        errors = []

        def writer():
            try:
                for i in range(20):
                    ev = CanonicalEvent(
                        event_id=f"thr_{threading.get_ident()}_{i}",
                        instrument_id="AAPL",
                        event_type=EventType.TRADE,
                        exchange_timestamp=100.0,
                        receive_timestamp=100.1,
                        processing_timestamp=100.2,
                        source="TEST",
                        sequence_number=i,
                        price=150.0 + i,
                        quantity=10.0,
                        quality_status=QualityStatus.VALID,
                        reasons=[],
                        raw_id=f"r_{threading.get_ident()}_{i}",
                    )
                    store.write_canonical_batch([ev])
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(20):
                    store.query_events(instrument_id="AAPL", limit=10)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)] + [
            threading.Thread(target=reader) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Encountered concurrency errors: {errors}"
    finally:
        store.close()
