"""
Tests for SHM Publisher Crash and Deadlock Prevention (v2.6 Reliability Milestone).

Verifies:
1. SHMReader.stream() detects publisher death on startup and yields PUBLISHER_DEAD.
2. SHMReader.stream() detects publisher crash/heartbeat freeze during spin-polling and terminates cleanly.
3. Mid-write crash leaving UNCOMMITTED slot terminates cleanly without hanging reader indefinitely.
4. MDRAPClient detects PUBLISHER_DEAD and closes/falls back cleanly without hanging.
"""

from __future__ import annotations

import os
import struct
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.shm import (
    SHMWriter,
    SHMReader,
    HAS_SHM,
    UNCOMMITTED,
    HEADER_SIZE,
    SLOT_SIZE,
)
from mdrap.client import MDRAPClient


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_publisher_dead_detection_on_stream_startup():
    """Verify stream() immediately yields PUBLISHER_DEAD if heartbeat is already stale."""
    shm_name = "test_shm_dead_startup"
    writer = SHMWriter(name=shm_name, slot_count=64)
    # Move heartbeat into the past (> 5s ago)
    struct.pack_into("<d", writer.shm.buf, 64, time.time() - 10.0)

    reader = SHMReader(name=shm_name)
    try:
        events = list(reader.stream(timeout=0.5, writer_dead_timeout=0.2))
        assert len(events) == 1
        assert events[0]["type"] == "PUBLISHER_DEAD"
        assert "Heartbeat stale" in events[0]["reason"]
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_publisher_crash_during_stream_spin():
    """Verify stream() yields PUBLISHER_DEAD and terminates when writer halts heartbeat during spin."""
    shm_name = "test_shm_dead_spin"
    writer = SHMWriter(name=shm_name, slot_count=64)

    # Write 2 valid events
    for i in range(1, 3):
        writer.write_tick(
            seq=i,
            symbol="BTC/USD",
            source="BINANCE",
            price=60000.0 + i,
            size=1.0,
            bid=59999.0,
            ask=60001.0,
            bid_size=1.0,
            ask_size=1.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=10.0,
        )

    reader = SHMReader(name=shm_name)
    try:
        gen = reader.stream(start_seq=1, writer_dead_timeout=0.15)
        # Read the two valid events
        ev1 = next(gen)
        assert ev1["seq"] == 1
        ev2 = next(gen)
        assert ev2["seq"] == 2

        # Simulate publisher process crash: freeze heartbeat in the past
        struct.pack_into("<d", writer.shm.buf, 64, time.time() - 1.0)

        # Next iteration in spin must detect stale publisher and yield PUBLISHER_DEAD
        t_spin_start = time.time()
        ev3 = next(gen)
        elapsed = time.time() - t_spin_start

        assert ev3["type"] == "PUBLISHER_DEAD"
        assert elapsed < 1.0  # Fast termination, no deadlock

        # Generator must now be exhausted
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_publisher_crash_mid_write_torn_slot():
    """Verify reader does not hang indefinitely when publisher crashes mid-write leaving UNCOMMITTED."""
    shm_name = "test_shm_mid_write_crash"
    writer = SHMWriter(name=shm_name, slot_count=64)

    # Seq 1 is valid
    writer.write_tick(
        seq=1,
        symbol="AAPL",
        source="NASDAQ",
        price=220.0,
        size=100.0,
        bid=219.9,
        ask=220.1,
        bid_size=50.0,
        ask_size=50.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=5.0,
    )

    # Seq 2 slot is invalidated with UNCOMMITTED
    slot_offset = HEADER_SIZE + ((2 & (64 - 1)) * SLOT_SIZE)
    struct.pack_into("<Q", writer.shm.buf, slot_offset, UNCOMMITTED)
    writer.update_heartbeat()

    reader = SHMReader(name=shm_name)
    try:
        gen = reader.stream(start_seq=1, writer_dead_timeout=0.1)
        ev1 = next(gen)
        assert ev1["seq"] == 1

        # Now simulate publisher process crash while holding slot 2: freeze heartbeat in past
        struct.pack_into("<d", writer.shm.buf, 64, time.time() - 2.0)

        # Trying to read seq 2 encounters UNCOMMITTED and dead publisher
        dead_ev = next(gen)
        assert dead_ev["type"] == "PUBLISHER_DEAD"

        with pytest.raises(StopIteration):
            next(gen)
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_client_clean_exit_on_publisher_crash(monkeypatch):
    """Verify MDRAPClient stream handles publisher crash without hanging."""
    shm_name = "test_shm_client_crash"
    writer = SHMWriter(name=shm_name, slot_count=64)

    writer.write_tick(
        seq=1,
        symbol="SPY",
        source="ARCA",
        price=550.0,
        size=500.0,
        bid=549.95,
        ask=550.05,
        bid_size=100.0,
        ask_size=100.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=8.0,
    )

    # Set writer timeout environment variable to 0.15s
    monkeypatch.setenv("MDRAP_SHM_WRITER_TIMEOUT", "0.15")

    client = MDRAPClient(transport="shm", shm_name=shm_name)
    client.connect()

    try:
        # Consume event 1
        stream_iter = client.stream(timeout=1.0)
        ev = next(stream_iter)
        assert ev.seq == 1
        assert ev.symbol == "SPY"

        # Freeze publisher heartbeat
        struct.pack_into("<d", writer.shm.buf, 64, time.time() - 2.0)

        # Client stream must terminate cleanly rather than deadlock
        remaining = list(stream_iter)
        assert remaining == []
        assert client.shm_reader is None
    finally:
        client.close()
        writer.close()
