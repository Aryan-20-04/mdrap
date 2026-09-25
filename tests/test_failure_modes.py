"""
MDRAP Architecture Failure Mode Tests
======================================
Adversarial failure mode tests for Native SHM ring buffer, seqlock synchronization,
asynchronous drain worker, and binary journal persistence under catastrophic events:
1. Writer crash & epoch rollover / invalidation
2. Seqlock torn-read recovery
3. Asynchronous drain worker crash and seamless restart
4. Journal partial-write / file truncation recovery
5. Buffer overrun and extreme watermark backpressure
"""

import os
import tempfile
import time
import struct
import pytest
from src.shm import (
    SHMWriter,
    SHMReader,
    HAS_SHM,
    HEADER_SIZE,
    SLOT_SIZE,
    HEADER_LINE1_STRUCT,
    MAGIC,
    VERSION,
)
from src.journal import BinaryJournal, BinaryJournalReader
from src.shm_drainer import SHMDrainWorker


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_failure_writer_crash_epoch_rollover():
    """
    Test writer crash and restart with a new epoch.
    Reader must detect epoch rollover via check_epoch_valid() and safely reject stale mapping.
    """
    shm_name = f"test_fail_epoch_{os.getpid()}_{time.time_ns()}"
    slot_count = 64

    # Writer Process 1 creates SHM
    w1 = SHMWriter(name=shm_name, slot_count=slot_count)
    try:
        # Write 10 ticks in epoch 1
        for seq in range(1, 11):
            w1.write_tick(
                seq=seq,
                symbol="AAPL",
                source="FEED1",
                price=150.0 + seq,
                size=100.0,
                bid=149.0 + seq,
                ask=151.0 + seq,
                bid_size=10.0,
                ask_size=10.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=1000.0 + seq,
                ingest_ts=1000.0 + seq,
                broadcast_ts=1000.0 + seq,
                engine_us=1.0,
            )

        # Consumer starts reading
        reader = SHMReader(name=shm_name)
        try:
            assert reader.check_epoch_valid() is True
            slot = reader.read_slot(1)
            assert slot is not None
            assert slot["seq"] == 1
            assert slot["sym"] == "AAPL"

            # Writer 1 crashes abruptly.
            # Next writer process starts up and advances the epoch in the existing buffer:
            new_epoch = w1.epoch_id + 1
            # Re-write Line 1 with new epoch
            HEADER_LINE1_STRUCT.pack_into(
                w1.shm.buf,
                0,
                MAGIC,
                VERSION,
                SLOT_SIZE,
                slot_count,
                0,
                new_epoch,
                0,
                b"\x00" * 32,
            )

            # Reader checks epoch validity
            assert reader.check_epoch_valid() is False, (
                "Reader must detect publisher epoch invalidation"
            )

            # Re-attaching reader connects to new epoch
            new_reader = SHMReader(name=shm_name)
            try:
                assert new_reader.epoch_id == new_epoch
                assert new_reader.check_epoch_valid() is True
            finally:
                new_reader.close()
        finally:
            reader.close()
    finally:
        w1.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_failure_seqlock_torn_read_handling():
    """
    Simulate a reader accessing a slot while a writer is mid-write (commit_seq=0 or torn).
    Reader seqlock check must safely return None rather than reading garbage data.
    """
    shm_name = f"test_fail_torn_{os.getpid()}_{time.time_ns()}"
    writer = SHMWriter(name=shm_name, slot_count=32)
    try:
        # Write valid tick seq 5
        writer.write_tick(
            seq=5,
            symbol="NVDA",
            source="FEEDX",
            price=500.0,
            size=10.0,
            bid=499.0,
            ask=501.0,
            bid_size=5.0,
            ask_size=5.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.0,
            broadcast_ts=1000.0,
            engine_us=1.0,
        )

        reader = SHMReader(name=shm_name)
        try:
            slot = reader.read_slot(5)
            assert slot is not None
            assert slot["price"] == 500.0

            # Simulate torn write: write half-written state into slot 5
            slot_idx = 5 & reader.mask
            slot_offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)
            # Temporarily invalidate commit_seq to 0 (in-progress lock)
            struct.pack_into("<Q", writer.shm.buf, slot_offset, 0)

            # Reader attempting to read seq 5 must reject it as incomplete
            torn_read = reader.read_slot(5)
            assert torn_read is None, (
                "Reader must reject slot while writer has not finalized commit_seq"
            )

            # Writer completes the write
            struct.pack_into("<Q", writer.shm.buf, slot_offset, 5)
            clean_read = reader.read_slot(5)
            assert clean_read is not None
            assert clean_read["seq"] == 5
        finally:
            reader.close()
    finally:
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_failure_drain_worker_crash_and_restart():
    """
    Verify that if the SHM drain worker crashes, a newly started worker
    seamlessly resumes from the exact last persisted sequence without data loss or duplication.
    """
    shm_name = f"test_fail_drain_restart_{os.getpid()}_{time.time_ns()}"
    writer = SHMWriter(name=shm_name, slot_count=512)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "restart_recovery.dbn")

        try:
            # Produce 50 ticks
            for seq in range(1, 51):
                writer.write_tick(
                    seq=seq,
                    symbol="GOOGL",
                    source="FEED1",
                    price=140.0 + seq,
                    size=20.0,
                    bid=139.0 + seq,
                    ask=141.0 + seq,
                    bid_size=10.0,
                    ask_size=10.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0 + seq,
                    ingest_ts=1000.0 + seq,
                    broadcast_ts=1000.0 + seq,
                    engine_us=1.0,
                )

            # Start worker 1 and drain first 30 ticks
            worker1 = SHMDrainWorker(
                shm_name=shm_name,
                journal_path=journal_path,
                batch_size=10,
                flush_interval_s=0.01,
            )
            worker1.start(start_seq=1)
            ok = worker1.drain_until(30, timeout=2.0)
            assert ok is True

            # Simulate worker 1 crash (cleanly unmap resources before restart)
            worker1.close()
            worker1 = None

            # Inspect journal to confirm last persisted seq
            j_reader = BinaryJournalReader(journal_path)
            last_persisted = j_reader.record_count
            assert last_persisted >= 30
            j_reader.close()

            # Produce 30 more ticks (seq 51 to 80)
            for seq in range(51, 81):
                writer.write_tick(
                    seq=seq,
                    symbol="GOOGL",
                    source="FEED1",
                    price=140.0 + seq,
                    size=20.0,
                    bid=139.0 + seq,
                    ask=141.0 + seq,
                    bid_size=10.0,
                    ask_size=10.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0 + seq,
                    ingest_ts=1000.0 + seq,
                    broadcast_ts=1000.0 + seq,
                    engine_us=1.0,
                )

            # Worker 2 restarts! It inspects journal and resumes from last_persisted + 1
            resume_seq = last_persisted + 1
            worker2 = SHMDrainWorker(
                shm_name=shm_name,
                journal_path=journal_path,
                batch_size=10,
                flush_interval_s=0.01,
            )
            worker2.start(start_seq=resume_seq)

            ok2 = worker2.drain_until(80, timeout=3.0)
            assert ok2 is True
            worker2.close()

            # Verify entire stream in journal is 100% sequential without duplicate or gap
            j_reader2 = BinaryJournalReader(journal_path)
            try:
                assert j_reader2.record_count == 80
                for idx in range(80):
                    rec = j_reader2.read_record(idx)
                    assert rec is not None
                    assert rec["seq"] == idx + 1
            finally:
                j_reader2.close()
        finally:
            writer.close()


def test_failure_journal_truncation_recovery():
    """
    Test recovery when a binary journal file is unexpectedly truncated mid-write.
    Journal must detect partial trailing bytes, restore to the last complete record boundary,
    and allow new writes cleanly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "truncated.dbn")
        with BinaryJournal(journal_path, initial_records=1024) as j1:
            for i in range(1, 26):
                j1.append_tick(
                    seq=i,
                    symbol="TSLA",
                    source="FEED1",
                    price=200.0 + i,
                    size=10.0,
                    status="VALID",
                )
            assert j1.record_count == 25

        # Artificially corrupt the file by appending 55 partial bytes (less than a full 128-byte record)
        actual_size = os.path.getsize(journal_path)
        with open(journal_path, "ab") as f:
            f.write(b"CORRUPT_PARTIAL_BYTES" * 3)

        corrupted_size = os.path.getsize(journal_path)
        assert corrupted_size > actual_size

        # Reopen journal: auto-healing should detect and truncate back to valid boundary
        with BinaryJournal(journal_path) as j2:
            assert j2.record_count == 25

            # Subsequent write should succeed at record index 25
            j2.append_tick(
                seq=26,
                symbol="TSLA",
                source="FEED1",
                price=226.0,
                size=10.0,
                status="VALID",
            )
            assert j2.record_count == 26

        with BinaryJournalReader(journal_path) as reader:
            assert reader.record_count == 26
            assert reader.read_record(24)["seq"] == 25
            assert reader.read_record(25)["seq"] == 26


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_failure_buffer_overrun_backpressure():
    """
    Test consumer behavior when writer outpaces consumer by more than the total ring capacity.
    Consumer must detect lag > slot_count, count the overrun/lap, and safely continue without crashing.
    """
    shm_name = f"test_fail_overrun_{os.getpid()}_{time.time_ns()}"
    slot_count = 32  # Small ring buffer
    writer = SHMWriter(name=shm_name, slot_count=slot_count)

    try:
        reader = SHMReader(name=shm_name)
        try:
            # Writer blasts 100 ticks without consumer reading
            for seq in range(1, 101):
                writer.write_tick(
                    seq=seq,
                    symbol="AMD",
                    source="FEED1",
                    price=120.0 + seq,
                    size=10.0,
                    bid=119.0 + seq,
                    ask=121.0 + seq,
                    bid_size=10.0,
                    ask_size=10.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0 + seq,
                    ingest_ts=1000.0 + seq,
                    broadcast_ts=1000.0 + seq,
                    engine_us=1.0,
                )

            # Target seq 1 was overwritten 68 ticks ago (slot_count=32).
            # Attempting to read old seq 1 must trigger lap detection:
            slot1 = reader.read_slot(1)
            assert slot1 is None
            assert reader.overrun_stats.total_laps >= 1, (
                "Reader must record overrun lap"
            )

            # Consumer jumps forward to an active recent sequence (e.g. latest - 10)
            latest = reader.read_latest_seq()
            assert latest >= 100

            active_seq = latest - 10
            slot_active = reader.read_slot(active_seq)
            assert slot_active is not None
            assert slot_active["seq"] == active_seq
            assert slot_active["sym"] == "AMD"
        finally:
            reader.close()
    finally:
        writer.close()
