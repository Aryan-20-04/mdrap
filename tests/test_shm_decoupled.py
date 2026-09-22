"""
MDRAP Phase 1 Verification: Decoupled Lock-Free SHM Ring Buffer Test Suite.

Verifies:
1. Two-phase commit protocol prevents torn reads without locks.
2. Publisher restart detection via epoch generation tracking & client auto-recovery.
3. Slow reader overrun detection and safe forward skipping without publisher stalls.
4. Heartbeat liveness and stale publisher detection.
5. Multi-tier transport fallback hierarchy (SHM -> TCP).
6. Fault isolation: reader crashes/failures cannot block or corrupt the writer.
7. Native C fastpath consistency with pure Python implementation.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shm import (
    SHMWriter,
    SHMReader,
    SHMOverrunStats,
    HEADER_SIZE,
    SLOT_SIZE,
    SLOT_STRUCT,
    HAS_SHM,
)
from client import MDRAPClient, MarketEvent
import fastpath


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_two_phase_commit_torn_read_prevention():
    """Verify that incomplete or torn writes return None rather than bad data."""
    shm_name = "test_shm_torn_read"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)

    try:
        # 1. Simulate Phase 1 failure: writer updated write_seq in header,
        # but slot commit_seq is still 0 (write in progress)
        struct.pack_into("<Q", writer.shm.buf, 20, 1)  # write_seq = 1

        # Reader tries to read seq 1: commit_seq in slot is 0 != 1 -> returns None
        assert reader.read_slot(1) is None

        # 2. Complete write properly
        writer.write_tick(
            seq=1,
            symbol="BTC/USD",
            source="KRAKEN",
            price=65000.0,
            size=0.5,
            bid=64990.0,
            ask=65010.0,
            bid_size=1.0,
            ask_size=1.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=12.0,
        )

        event = reader.read_slot(1)
        assert event is not None
        assert event["seq"] == 1
        assert event["sym"] == "BTC/USD"
        assert event["price"] == 65000.0

        # 3. Future sequence check: seq > latest write_seq -> returns None
        assert reader.read_slot(2) is None

    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_publisher_restart_epoch_recovery():
    """Verify that if the publisher restarts with a new epoch, reader and client handle it cleanly."""
    shm_name = "test_shm_restart"

    # Publisher 1 starts with Epoch A
    writer1 = SHMWriter(name=shm_name, slot_count=64)
    epoch1 = writer1.epoch_id
    writer1.write_tick(
        seq=1,
        symbol="ETH/USD",
        source="BINANCE",
        price=3500.0,
        size=2.0,
        bid=3499.0,
        ask=3501.0,
        bid_size=5.0,
        ask_size=5.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=10.0,
    )

    reader = SHMReader(name=shm_name)
    assert reader.epoch_id == epoch1
    assert reader.check_epoch_valid() is True

    # Publisher restarts! Writer 1 is closed, Writer 2 starts with new epoch
    writer1.close()

    writer2 = SHMWriter(name=shm_name, slot_count=64)
    epoch2 = writer2.epoch_id
    assert epoch2 != epoch1

    # Old reader detects epoch mismatch
    assert reader.check_epoch_valid() is False
    reader.close()

    # Re-attaching gets the new epoch
    new_reader = SHMReader(name=shm_name)
    assert new_reader.epoch_id == epoch2
    assert new_reader.check_epoch_valid() is True

    new_reader.close()
    writer2.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_client_streaming_epoch_auto_recovery():
    """Verify MDRAPClient transparently re-attaches when publisher restarts during live stream."""
    shm_name = "test_client_epoch_recover"
    writer = SHMWriter(name=shm_name, slot_count=64)

    # Write initial event
    writer.write_tick(
        seq=1,
        symbol="BTC/USD",
        source="BINANCE",
        price=80000.0,
        size=1.0,
        bid=79999.0,
        ask=80001.0,
        bid_size=1.0,
        ask_size=1.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=15.0,
    )

    client = MDRAPClient(transport="shm", shm_name=shm_name)
    client.connect()
    assert client.transport_type == "SHM"

    # Consume 1 event
    events = []
    for ev in client.stream(timeout=0.2, max_events=1):
        events.append(ev)
    assert len(events) == 1
    assert events[0].seq == 1

    # Now restart writer in the background
    writer.close()
    writer2 = SHMWriter(name=shm_name, slot_count=64)
    writer2.write_tick(
        seq=1,
        symbol="BTC/USD",
        source="BINANCE",
        price=80500.0,
        size=1.5,
        bid=80499.0,
        ask=80501.0,
        bid_size=2.0,
        ask_size=2.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1001.0,
        ingest_ts=1001.001,
        broadcast_ts=1001.002,
        engine_us=14.0,
    )

    # Client streaming resumes and catches the new epoch
    events2 = []
    for ev in client.stream(timeout=0.2, max_events=1):
        events2.append(ev)

    assert len(events2) == 1
    assert events2[0].price == 80500.0

    client.close()
    writer2.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_slow_reader_overrun_detection():
    """Verify slow reader detects buffer overrun, skips forward, and records telemetry."""
    shm_name = "test_shm_overrun"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)

    try:
        # Write 200 events into a 64-slot ring buffer
        for i in range(1, 201):
            writer.write_tick(
                seq=i,
                symbol="SOL/USD",
                source="COINBASE",
                price=100.0 + i,
                size=1.0,
                bid=100.0 + i - 0.1,
                ask=100.0 + i + 0.1,
                bid_size=1.0,
                ask_size=1.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=1000.0 + i,
                ingest_ts=1000.0 + i,
                broadcast_ts=1000.0 + i,
                engine_us=5.0,
            )

        assert reader.read_latest_seq() == 200

        # Reader attempting to read an old slot (seq 1) gets None and increments lap stats
        assert reader.read_slot(1) is None
        assert reader.overrun_stats.total_laps >= 1

        # Stream generator starting from seq 1 catches up without deadlocking
        streamed = list(reader.stream(start_seq=1, timeout=0.1, max_events=10))
        assert len(streamed) == 10
        # The first event streamed should be from the un-overwritten window
        assert streamed[0]["seq"] >= (200 - 64 + 1)
        assert reader.overrun_stats.skipped_ticks > 0

    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_heartbeat_and_writer_liveness():
    """Verify heartbeat updates and detection of stale/frozen publishers."""
    shm_name = "test_shm_heartbeat"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)

    try:
        # Fresh heartbeat
        writer.update_heartbeat()
        assert reader.is_writer_alive(max_stale_s=2.0) is True

        # Manually alter heartbeat to be 10 seconds in the past
        past_ts = time.time() - 10.0
        struct.pack_into("<d", writer.shm.buf, 64, past_ts)

        # Reader detects stale publisher
        assert reader.is_writer_alive(max_stale_s=2.0) is False

        # Publisher resumes and updates heartbeat
        writer.update_heartbeat()
        assert reader.is_writer_alive(max_stale_s=2.0) is True

    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_transport_fallback_hierarchy():
    """Verify client transport selection and fallback behavior."""
    # 1. SHM unavailable -> fallback to TCP socket in 'auto' mode
    non_existent = "non_existent_shm_segment_test_9999"
    client_auto = MDRAPClient(transport="auto", shm_name=non_existent)
    # Mock socket connect so it doesn't fail trying to reach localhost:9876
    with patch("socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        client_auto.connect()
        # Should have fallen back from SHM to TCP
        assert client_auto.shm_reader is None
        assert client_auto.sock is not None
        assert client_auto.transport_type in ("JSON_TCP", "BINARY_TCP")
    client_auto.close()

    # 2. Strict SHM mode raises ConnectionError when SHM unavailable
    client_shm = MDRAPClient(transport="shm", shm_name=non_existent)
    with pytest.raises(ConnectionError, match="Shared memory '.*' unavailable"):
        client_shm.connect()
    client_shm.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_decoupled_fault_isolation_reader_crash():
    """Verify writer is unaffected by reader creation, usage, and sudden termination."""
    shm_name = "test_shm_isolation"
    writer = SHMWriter(name=shm_name, slot_count=128)

    # Launch 5 concurrent readers that read and terminate abruptly
    def reader_worker():
        try:
            r = SHMReader(name=shm_name)
            for _ in range(20):
                r.read_latest_seq()
                time.sleep(0.001)
            # Sudden unmanaged close
            r.close()
        except Exception:
            pass

    threads = [threading.Thread(target=reader_worker) for _ in range(5)]
    for t in threads:
        t.start()

    # Writer continues publishing uninterrupted
    for seq in range(1, 100):
        writer.write_tick(
            seq=seq,
            symbol="BTC/USD",
            source="BINANCE",
            price=50000.0 + seq,
            size=1.0,
            bid=49999.0,
            ask=50001.0,
            bid_size=1.0,
            ask_size=1.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=8.0,
        )

    for t in threads:
        t.join()

    # Verify writer state is intact
    assert writer._write_seq == 99

    # Fresh reader can read final seq
    final_reader = SHMReader(name=shm_name)
    assert final_reader.read_latest_seq() == 99
    ev = final_reader.read_slot(99)
    assert ev is not None
    assert ev["price"] == pytest.approx(50099.0)

    final_reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_native_fastpath_read_consistency():
    """Verify native C fastpath reader produces identical results to Python reader."""
    if not fastpath.has_native_shm():
        pytest.skip("Native C fastpath library not available")

    shm_name = "test_shm_native_consistency"
    writer = SHMWriter(name=shm_name, slot_count=128)
    reader = SHMReader(name=shm_name)

    try:
        t_now = time.time()
        # Write Tick
        writer.write_tick(
            seq=10,
            symbol="NVDA",
            source="NASDAQ",
            price=125.75,
            size=500.0,
            bid=125.70,
            ask=125.80,
            bid_size=1000.0,
            ask_size=1200.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=t_now - 0.002,
            ingest_ts=t_now - 0.001,
            broadcast_ts=t_now,
            engine_us=16.8,
        )

        py_tick = reader.read_slot(10)
        c_tick = fastpath.native_shm_read_slot(
            reader.shm.buf,
            reader.slot_count,
            10,
        )

        assert py_tick is not None
        assert c_tick is not None
        assert py_tick["type"] == c_tick["type"] == "TICK"
        assert py_tick["seq"] == c_tick["seq"] == 10
        assert py_tick["sym"] == c_tick["sym"] == "NVDA"
        assert py_tick["source"] == c_tick["source"] == "NASDAQ"
        assert py_tick["price"] == pytest.approx(c_tick["price"]) == pytest.approx(125.75)
        assert py_tick["size"] == pytest.approx(c_tick["size"]) == pytest.approx(500.0)
        assert py_tick["bid"] == pytest.approx(c_tick["bid"]) == pytest.approx(125.70)
        assert py_tick["ask"] == pytest.approx(c_tick["ask"]) == pytest.approx(125.80)
        assert py_tick["status"] == c_tick["status"] == "VALID"
        assert py_tick["is_crossed"] == c_tick["is_crossed"] is False

    finally:
        reader.close()
        writer.close()
