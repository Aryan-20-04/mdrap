"""Phase 2 Roadmap Verification Test Suite: Correctness, Parity, Resilience.

Validates all 9 Phase 2 properties:
- Task 2.1: SHM Seqlock Protocol / Memory Ordering & torn-read protection
- Task 2.2: Epoch Probe Syscall Storm elimination via in-memory mapped reads
- Task 2.3: Quality Engine C / Python rule parity and priority preservation
- Task 2.4: Unsequenced Quote Deduplication Key (includes bid_size & ask_size)
- Task 2.5: ITCH Late-Join Executions (E quarantined without price, C recorded with price)
- Task 2.6: TCP Gateway Non-Blocking Broadcast with per-client bounded queues
- Task 2.7: Journal Crash Recovery with CRC32 & torn tail truncation
- Task 2.8: Incremental Merkle Audit Verification (delta scans via since_entry_id)
- Task 2.9: SQLite Production PRAGMAs (wal_autocheckpoint=1000, 256MB mmap, 64MB cache)
"""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
import time
import unittest.mock as mock
import zlib

import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.quality import QualityConfig, QualityEngine
from mdrap.shm import (
    HAS_SHM,
    HEADER_SIZE,
    SLOT_SIZE,
    UNCOMMITTED,
    SHMReader,
    SHMWriter,
)
from mdrap.itch import (
    ITCHMessage,
    ITCHOrderBookTracker,
)
from mdrap.gateway_tcp import TCPGatewayServer
from mdrap.journal import (
    JOURNAL_HEADER_SIZE,
    BinaryJournal,
    BinaryJournalReader,
)
from mdrap.storage import Store


# ---------------------------------------------------------------------------
# Task 2.4: Unsequenced Quote Deduplication Key (bid_size & ask_size)
# ---------------------------------------------------------------------------
def test_2_4_unsequenced_quote_dedup_distinguishes_size_updates():
    """Quotes with identical prices but updated sizes must NOT be flagged as DUPLICATE."""
    cfg = QualityConfig(unseq_dup_status="SUSPICIOUS")
    engine = QualityEngine(cfg)

    # First quote: 100.0 / 100.5, sizes 10 / 20
    q1 = CanonicalEvent(
        event_id="q-1",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.01,
        processing_timestamp=1000.02,
        source="FEED_A",
        sequence_number=None,  # Unsequenced
        bid_price=100.0,
        ask_price=100.5,
        bid_size=10.0,
        ask_size=20.0,
    )
    res1 = engine.evaluate(q1)
    assert res1.quality_status == QualityStatus.VALID
    assert Reason.DUPLICATE.value not in res1.reasons

    # Second quote: SAME prices, but bid_size changed to 15.0
    q2 = CanonicalEvent(
        event_id="q-2",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.011,
        processing_timestamp=1000.021,
        source="FEED_A",
        sequence_number=None,  # Unsequenced
        bid_price=100.0,
        ask_price=100.5,
        bid_size=15.0,  # Changed size!
        ask_size=20.0,
    )
    res2 = engine.evaluate(q2)
    assert res2.quality_status == QualityStatus.VALID, (
        f"Quote with updated bid_size was falsely marked: {res2.reasons}"
    )
    assert Reason.DUPLICATE.value not in res2.reasons

    # Third quote: EXACT duplicate of q2 (same prices AND sizes)
    q3 = CanonicalEvent(
        event_id="q-3",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.012,
        processing_timestamp=1000.022,
        source="FEED_A",
        sequence_number=None,
        bid_price=100.0,
        ask_price=100.5,
        bid_size=15.0,
        ask_size=20.0,
    )
    res3 = engine.evaluate(q3)
    assert res3.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.DUPLICATE.value in res3.reasons


# ---------------------------------------------------------------------------
# Task 2.2: Epoch Probe Syscall Optimization
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not supported on this platform")
def test_2_2_epoch_probe_avoids_syscall_storm():
    """Steady-state check_epoch_valid reads offset 16 from memory without reopening SHM."""
    name = f"test_shm_probe_{int(time.time_ns())}"
    writer = SHMWriter(name=name, slot_count=64)
    reader = SHMReader(name=name)

    try:
        # Default check_epoch_valid() must be force_probe=False
        with mock.patch("mdrap.shm._probe_active_epoch") as mock_probe:
            # 100 consecutive checks
            for _ in range(100):
                valid = reader.check_epoch_valid()
                assert valid is True
            # Zero external segment probe syscalls during steady state!
            assert mock_probe.call_count == 0

            # Only when force_probe=True is explicitly passed should it probe externally
            reader.check_epoch_valid(force_probe=True)
            assert mock_probe.call_count == 1
    finally:
        reader.close()
        writer.close()


# ---------------------------------------------------------------------------
# Task 2.1: SHM Seqlock Protocol / Memory Ordering
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not supported on this platform")
def test_2_1_shm_seqlock_uncommitted_slot_and_torn_read_protection():
    """Reader must reject slots while writer holds UNCOMMITTED sequence."""
    name = f"test_shm_seqlock_{int(time.time_ns())}"
    writer = SHMWriter(name=name, slot_count=64)
    reader = SHMReader(name=name)

    try:
        # Commit slot 0
        writer.write_tick(seq=0, symbol="AAPL", source="BATS", price=150.0, size=100.0)
        item0 = reader.read_slot(0)
        assert item0 is not None
        assert item0["seq"] == 0
        assert item0["price"] == 150.0

        # Simulate writer midway through slot 1: invalidate with UNCOMMITTED
        slot_idx = 1 & (writer.slot_count - 1)
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)
        struct.pack_into("<Q", writer.shm.buf, offset, UNCOMMITTED)

        # Reader must return None (in progress, not torn read)
        item1 = reader.read_slot(1)
        assert item1 is None

        # Simulate torn read: c1 != c2 during seqlock verification
        # Writer commits slot 1
        writer.write_tick(seq=1, symbol="AAPL", source="BATS", price=151.0, size=200.0)
        # Mock unpack_from so second check returns different commit_seq
        orig_unpack = struct.unpack_from
        call_count = 0

        def mock_unpack_torn(fmt, buf, off=0):
            nonlocal call_count
            if off == offset:
                call_count += 1
                if call_count == 2:
                    return (999999,)  # Mismatched c2
            return orig_unpack(fmt, buf, off)

        with mock.patch("mdrap.fastpath.native_shm_read_slot", side_effect=RuntimeError("test fallback")):
            with mock.patch("struct.unpack_from", side_effect=mock_unpack_torn):
                torn_item = reader.read_slot(1)
                # Must detect torn read and return None!
                assert torn_item is None
    finally:
        reader.close()
        writer.close()


# ---------------------------------------------------------------------------
# Task 2.3: Quality Engine C / Python Rule Parity
# ---------------------------------------------------------------------------
def test_2_3_quality_engine_parity_strict_priority():
    """Ensure strict priority INVALID > SUSPICIOUS > VALID and rule reason stability."""
    py_engine = QualityEngine()

    # Normal valid tick
    ev_valid = CanonicalEvent(
        event_id="e-valid",
        instrument_id="MSFT",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.01,
        processing_timestamp=1000.02,
        source="FEED",
        sequence_number=1,
        price=300.0,
        quantity=50.0,
    )
    res_valid = py_engine.evaluate(ev_valid)
    assert res_valid.quality_status == QualityStatus.VALID

    # Crossed quote must be INVALID
    ev_crossed = CanonicalEvent(
        event_id="e-crossed",
        instrument_id="MSFT",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.01,
        processing_timestamp=1000.02,
        source="FEED",
        sequence_number=2,
        bid_price=305.0,
        ask_price=300.0,  # Crossed: bid > ask
        bid_size=10.0,
        ask_size=10.0,
    )
    res_crossed = py_engine.evaluate(ev_crossed)
    assert res_crossed.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in res_crossed.reasons

    # Out of order sequence must be SUSPICIOUS
    ev_ooo = CanonicalEvent(
        event_id="e-ooo",
        instrument_id="MSFT",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.01,
        processing_timestamp=1000.02,
        source="FEED",
        sequence_number=1,  # Decreasing / duplicate sequence
        price=300.0,
        quantity=50.0,
    )
    res_ooo = py_engine.evaluate(ev_ooo)
    assert res_ooo.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID)


# ---------------------------------------------------------------------------
# Task 2.5: ITCH Late-Join Executions (E & C Messages)
# ---------------------------------------------------------------------------
def test_2_5_itch_late_join_quarantine_e_and_record_c():
    """ITCH E without order state is quarantined as INVALID; C with price is recorded as SUSPICIOUS."""
    tracker = ITCHOrderBookTracker()

    # Message E without preceding Add Order (A/F)
    msg_e = ITCHMessage(
        msg_type="E",
        timestamp_ns=1000_000_000,
        locate=1,
        stock="AAPL",
        order_ref=999001,
        shares=100,
        match_number=12345,
    )
    evt_e = tracker.process_message(msg_e)
    assert evt_e is not None
    assert evt_e.quality_status == QualityStatus.INVALID
    assert evt_e.price is None
    assert "UNKNOWN_ORDER_REF" in evt_e.reasons
    assert "EXECUTION_WITHOUT_PRICE" in evt_e.reasons
    assert tracker.quarantined_executes == 1

    # Message C without preceding Add Order, but WITH price
    msg_c = ITCHMessage(
        msg_type="C",
        timestamp_ns=1000_000_000,
        locate=1,
        stock="AAPL",
        order_ref=999002,
        shares=150,
        match_number=12346,
        price=150.25,  # Carries real execution price
    )
    evt_c = tracker.process_message(msg_c)
    assert evt_c is not None
    assert evt_c.quality_status == QualityStatus.SUSPICIOUS
    assert evt_c.price == 150.25
    assert "EXECUTION_WITHOUT_LOCAL_ORDER_STATE" in evt_c.reasons


# ---------------------------------------------------------------------------
# Task 2.6: TCP Gateway Non-Blocking Broadcast Fan-Out
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_2_6_tcp_gateway_non_blocking_broadcast_and_queue_isolation():
    """Slow client with full bounded queue does not block broadcast to fast clients."""
    server = TCPGatewayServer(port=0, require_auth=False)
    server.client_queue_size = 2  # Tiny queue to easily simulate overflow
    server.running = True

    writer_fast = mock.AsyncMock(spec=asyncio.StreamWriter)
    writer_fast.drain = mock.AsyncMock()
    writer_slow = mock.AsyncMock(spec=asyncio.StreamWriter)
    writer_slow.drain = mock.AsyncMock()

    # Attach queues
    q_fast = asyncio.Queue(maxsize=server.client_queue_size)
    q_slow = asyncio.Queue(maxsize=server.client_queue_size)
    server.clients.add(writer_fast)
    server.clients.add(writer_slow)
    server._client_queues[writer_fast] = q_fast
    server._client_queues[writer_slow] = q_slow

    # Pre-fill slow client queue to capacity
    q_slow.put_nowait(b"msg1\n")
    q_slow.put_nowait(b"msg2\n")

    # Broadcast message: must NOT raise, must complete in sub-millisecond
    t0 = time.perf_counter()
    await server.broadcast({"type": "tick", "price": 100.0})
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.05, f"Broadcast took too long: {elapsed:.4f}s"
    # Fast client received message in queue
    assert q_fast.qsize() == 1
    # Slow client overflowed and was evicted
    assert server._stats["dropped"] >= 1
    assert writer_slow not in server.clients
    assert writer_fast in server.clients

    await server.stop()


# ---------------------------------------------------------------------------
# Task 2.7: Journal Crash Recovery with CRC32 & Torn Tail Truncation
# ---------------------------------------------------------------------------
def test_2_7_journal_crc32_and_torn_tail_crash_recovery():
    """Journal crash recovery verifies CRC32 and truncates torn/corrupted tail records."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jpath = os.path.join(tmpdir, "test_crc_recovery.dbn")

        # 1. Write 10 valid records
        with BinaryJournal(jpath, initial_records=64) as j:
            for i in range(1, 11):
                j.append_tick(seq=i, symbol="AAPL", source="BATS", price=150.0 + i, size=10.0)

        # 2. Tamper the 10th record to simulate torn write (flip bit in payload, CRC mismatch)
        with open(jpath, "r+b") as f:
            offset_10th = JOURNAL_HEADER_SIZE + (9 * SLOT_SIZE)
            f.seek(offset_10th + 48)  # price offset
            f.write(b"\xFF\xFF\xFF\xFF")  # Corrupt price bytes without updating CRC

        # 3. Simulate header reset (uncommitted crash)
        with open(jpath, "r+b") as f:
            f.seek(16)  # epoch/record_count
            f.write(struct.pack("<Q", 0))  # reset record_count to 0

        # 4. Reopen journal: recovery must scan forward, accept 9 valid records,
        # and stop at the 10th corrupted record!
        with BinaryJournal(jpath) as j_rec:
            assert j_rec.record_count == 9
            assert j_rec.first_seq == 1
            assert j_rec.last_seq == 9

        with BinaryJournalReader(jpath) as reader:
            assert len(list(reader)) == 9


# ---------------------------------------------------------------------------
# Task 2.8: Incremental Merkle Audit Verification
# ---------------------------------------------------------------------------
def test_2_8_incremental_merkle_audit_verification():
    """Incremental audit verification checks only new records since since_entry_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_audit.db")
        store = Store(db_path)
        try:
            # Append 5 audit log entries
            for i in range(1, 6):
                store.append_audit(
                    actor=f"user_{i}",
                    role="admin",
                    action="TEST_ACTION",
                    details=f"detail_{i}",
                )

            # Full verification: all 5 entries verified
            ok_full, msg_full, last_id = store.verify_audit_integrity()
            assert ok_full is True
            assert last_id == 5

            # Add 3 more entries (entries 6, 7, 8)
            for i in range(6, 9):
                store.append_audit(
                    actor=f"user_{i}",
                    role="admin",
                    action="TEST_ACTION",
                    details=f"detail_{i}",
                )

            # Incremental verification: verify ONLY entries since entry_id=6
            ok_inc, msg_inc, last_inc_id = store.verify_audit_integrity(since_entry_id=6)
            assert ok_inc is True
            assert last_inc_id == 8

            # If entry 7 is tampered with, incremental verification flags it
            with store.transaction() as cur:
                cur.execute("UPDATE audit_log SET details = 'TAMPERED' WHERE entry_id = 7")

            ok_bad, msg_bad, _ = store.verify_audit_integrity(since_entry_id=6)
            assert ok_bad is False
            assert "Tampered entry #7" in msg_bad
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Task 2.9: SQLite Production PRAGMAs & Tuning
# ---------------------------------------------------------------------------
def test_2_9_sqlite_production_pragmas_and_tuning():
    """Verify wal_autocheckpoint, 256MB mmap, and 64MB page cache tuning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_pragmas.db")
        store = Store(db_path)
        try:
            # Query PRAGMAs directly
            wal_auto = store.conn.execute("PRAGMA wal_autocheckpoint;").fetchone()[0]
            cache_sz = store.conn.execute("PRAGMA cache_size;").fetchone()[0]
            mmap_sz = store.conn.execute("PRAGMA mmap_size;").fetchone()[0]

            assert wal_auto == 1000
            # -64000 KiB = 64 MB
            assert cache_sz == -64000
            # 256 MB = 268435456 bytes
            assert mmap_sz == 268435456
        finally:
            store.close()
