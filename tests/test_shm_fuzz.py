"""
Fuzz testing and boundary resilience for MDRAP Shared Memory Ring Buffer Engine v3.
Tests malformed headers, torn sequences, buffer boundaries, epoch mutations, and invalid slot states.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shm import (
    SHMWriter,
    SHMReader,
    MAGIC,
    VERSION,
    SLOT_SIZE,
    HEADER_SIZE,
    UNCOMMITTED,
    HEADER_LINE1_STRUCT,
    HEADER_LINE2_STRUCT,
    SLOT_STRUCT,
    PAYLOAD_STRUCT,
    HAS_SHM,
)

if HAS_SHM:
    from multiprocessing.shared_memory import SharedMemory


def _cleanup_shm(name: str) -> None:
    if not HAS_SHM:
        return
    try:
        s = SharedMemory(name=name, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
class TestSHMFuzzAndBoundaries:
    """Test suite targeting malformed state and edge cases in SHM ring buffer."""

    def setup_method(self, method):
        """Clean up any stale shared memory segments before each test."""
        names = [
            "test_fuzz_too_small",
            "test_fuzz_corrupt_magic",
            "test_fuzz_version",
            "test_fuzz_slot_size",
            "test_fuzz_non_power_two",
            "test_fuzz_oversized_slot_count",
            "test_fuzz_uncommitted",
            "test_fuzz_torn_seqlock",
            "test_fuzz_epoch",
            "test_fuzz_overrun",
            "test_fuzz_future",
            "test_fuzz_extreme_numerics",
        ]
        for n in names:
            _cleanup_shm(n)

    def test_malformed_buffer_too_small(self):
        """Buffer smaller than minimum header + 2 slots or uninitialized must be rejected."""
        name = "test_fuzz_too_small"
        _cleanup_shm(name)
        shm = SharedMemory(name=name, create=True, size=64)
        try:
            with pytest.raises(
                ValueError, match=r"(SHM buffer too small|Invalid SHM magic)"
            ):
                SHMReader(name=name)
        finally:
            shm.close()
            shm.unlink()

    def test_corrupted_magic_bytes(self):
        """Corrupted magic header bytes must raise ValueError."""
        name = "test_fuzz_corrupt_magic"
        writer = SHMWriter(name=name, slot_count=16)
        try:
            # Corrupt the first 4 bytes
            struct.pack_into("<4s", writer.shm.buf, 0, b"CORR")
            with pytest.raises(ValueError, match="Invalid SHM magic"):
                SHMReader(name=name)
        finally:
            writer.close()

    def test_unsupported_version(self):
        """Unsupported version field must raise ValueError."""
        name = "test_fuzz_version"
        writer = SHMWriter(name=name, slot_count=16)
        try:
            struct.pack_into("<H", writer.shm.buf, 4, 99)
            with pytest.raises(ValueError, match="Unsupported SHM version"):
                SHMReader(name=name)
        finally:
            writer.close()

    def test_unexpected_slot_size(self):
        """Mismatched slot size header must raise ValueError."""
        name = "test_fuzz_slot_size"
        writer = SHMWriter(name=name, slot_count=16)
        try:
            struct.pack_into("<H", writer.shm.buf, 6, 64)
            with pytest.raises(ValueError, match="Unexpected slot size"):
                SHMReader(name=name)
        finally:
            writer.close()

    def test_invalid_slot_count_not_power_of_two(self):
        """Non-power-of-2 slot count must raise ValueError."""
        name = "test_fuzz_non_power_two"
        writer = SHMWriter(name=name, slot_count=16)
        try:
            struct.pack_into("<I", writer.shm.buf, 8, 15)  # 15 is not power of 2
            with pytest.raises(ValueError, match="Invalid slot count"):
                SHMReader(name=name)
        finally:
            writer.close()

    def test_buffer_truncated_for_declared_slot_count(self):
        """Declared slot count exceeding buffer size must raise ValueError."""
        name = "test_fuzz_oversized_slot_count"
        writer = SHMWriter(name=name, slot_count=16)
        try:
            struct.pack_into("<I", writer.shm.buf, 8, 65536)
            with pytest.raises(ValueError, match="Buffer truncated"):
                SHMReader(name=name)
        finally:
            writer.close()

    def test_uncommitted_slot_returns_none(self):
        """Reading a slot in UNCOMMITTED state must safely return None."""
        name = "test_fuzz_uncommitted"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            # Set head_seq = 2 so reader expects seq 1 to exist
            struct.pack_into("<Q", writer.shm.buf, 24, 2)

            # Slot 1 is still in UNCOMMITTED state (0xFFFFFFFFFFFFFFFF)
            slot_offset = HEADER_SIZE + (1 * SLOT_SIZE)
            struct.pack_into("<Q", writer.shm.buf, slot_offset, UNCOMMITTED)

            assert reader.read_slot(1) is None
        finally:
            reader.close()
            writer.close()

    def test_torn_read_seqlock_mismatch(self):
        """Simulated concurrent overwrite tearing the commit sequence must return None."""
        name = "test_fuzz_torn_seqlock"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            # Write a valid tick
            writer.write_tick(
                seq=1,
                symbol="BTC/USD",
                source="FEEDX",
                price=80000.0,
                size=1.0,
                bid=79999.0,
                ask=80001.0,
                bid_size=10.0,
                ask_size=10.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=1000.0,
                ingest_ts=1000.001,
                broadcast_ts=1000.002,
                engine_us=10.0,
            )

            # Manually corrupt commit_seq to simulate torn write
            slot_offset = HEADER_SIZE + (1 * SLOT_SIZE)
            struct.pack_into("<Q", writer.shm.buf, slot_offset, 999)

            # Reading seq 1 must detect mismatch and return None
            assert reader.read_slot(1) is None
        finally:
            reader.close()
            writer.close()

    def test_epoch_mutation_detection(self):
        """Mutating the epoch in the writer header must invalidate reader's epoch check."""
        name = "test_fuzz_epoch"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            assert reader.check_epoch_valid() is True

            # Mutate writer epoch
            orig_epoch = reader.epoch_id
            struct.pack_into("<Q", writer.shm.buf, 16, orig_epoch ^ 0xDEADBEEF)

            assert reader.check_epoch_valid() is False
        finally:
            reader.close()
            writer.close()

    def test_overrun_and_lap_detection(self):
        """Reading a sequence older than buffer capacity records a lap and returns None."""
        name = "test_fuzz_overrun"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            # Write 40 ticks into 16-slot ring
            for i in range(1, 41):
                writer.write_tick(
                    seq=i,
                    symbol="ETH/USD",
                    source="FEEDX",
                    price=3000.0 + i,
                    size=1.0,
                    bid=2999.0,
                    ask=3001.0,
                    bid_size=5.0,
                    ask_size=5.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0,
                    ingest_ts=1000.001,
                    broadcast_ts=1000.002,
                    engine_us=5.0,
                )

            assert reader.read_latest_seq() == 40

            # Seq 5 is older than 40 - 16 = 24, must be flagged as lap
            assert reader.read_slot(5) is None
            assert reader.overrun_stats.total_laps >= 1
            assert reader.overrun_stats.last_lap_seq == 5

            # Seq 35 is recent (within 16 slots), must read successfully
            ev = reader.read_slot(35)
            assert ev is not None
            assert ev["seq"] == 35
            assert ev["sym"] == "ETH/USD"
        finally:
            reader.close()
            writer.close()

    def test_future_sequence_returns_none(self):
        """Requesting sequence beyond current published head returns None."""
        name = "test_fuzz_future"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            assert reader.read_slot(100) is None
            writer.write_tick(
                seq=1,
                symbol="BTC/USD",
                source="FEEDX",
                price=50000.0,
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
                engine_us=5.0,
            )
            assert reader.read_slot(2) is None
        finally:
            reader.close()
            writer.close()

    def test_extreme_numeric_payloads(self):
        """Extreme numeric values (large floats, zero sizes, inf) must unpack safely."""
        name = "test_fuzz_extreme_numerics"
        writer = SHMWriter(name=name, slot_count=16)
        reader = SHMReader(name=name)
        try:
            writer.write_tick(
                seq=1,
                symbol="BTC/USD",
                source="FEEDX",
                price=1e12,
                size=1e-8,
                bid=0.0,
                ask=1e12 + 100.0,
                bid_size=0.0,
                ask_size=999999999.0,
                status="SUSPICIOUS",
                is_crossed=False,
                exchange_ts=1e9,
                ingest_ts=1e9 + 0.001,
                broadcast_ts=1e9 + 0.002,
                engine_us=0.045,
            )
            ev = reader.read_slot(1)
            assert ev is not None
            assert ev["price"] == 1e12
            assert ev["size"] == 1e-8
            assert ev["status"] == "SUSPICIOUS"
        finally:
            reader.close()
            writer.close()
