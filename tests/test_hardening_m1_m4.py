import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shm import SHMWriter, SHMReader, HAS_SHM, VERSION, HEADER_SIZE, SLOT_SIZE


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_m4_zero_filled_buffer_no_phantom_tick():
    """M4: A zero-filled or newly initialized buffer must never yield a phantom tick for seq 0."""
    from multiprocessing.shared_memory import SharedMemory

    shm_name = "test_m4_zero_fill"
    try:
        raw_shm = SharedMemory(
            name=shm_name, create=True, size=HEADER_SIZE + 256 * SLOT_SIZE
        )
        raw_shm.buf[:] = b"\x00" * len(raw_shm.buf)
        # Attempting to read slot 0 from an empty/uninitialized segment
        # In v3, reader validates header or read_slot returns None (head==0)
        try:
            reader = SHMReader(name=shm_name)
            res = reader.read_slot(0)
            assert res is None, f"Expected None on zero-filled buffer, got {res}"
            reader.close()
        except ValueError:
            # Header validation rejected zero-filled magic/version
            pass
    finally:
        try:
            raw_shm.close()
            raw_shm.unlink()
        except Exception:
            pass


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_m5_m6_present_bitmask_and_unknown_status():
    """M5/M6: None price/bid/ask reads back as None (not 0.0), and unknown status is not VALID."""
    shm_name = "test_m5_m6"
    writer = SHMWriter(name=shm_name, slot_count=128)
    reader = SHMReader(name=shm_name)
    try:
        t_now = time.time()
        writer.write_tick(
            seq=0,
            symbol="BTC/USD",
            source="TEST",
            price=None,
            size=10.0,
            bid=None,
            ask=None,
            bid_size=None,
            ask_size=None,
            status="BOGUS_STATUS",
            is_crossed=False,
            exchange_ts=t_now,
            ingest_ts=t_now,
            broadcast_ts=t_now,
            engine_us=1.0,
        )
        ev = reader.read_slot(0)
        assert ev is not None
        assert ev["price"] is None, f"Expected None, got {ev['price']}"
        assert ev["bid"] is None, f"Expected None, got {ev['bid']}"
        assert ev["ask"] is None, f"Expected None, got {ev['ask']}"
        assert ev["status"] != "VALID", f"Expected non-VALID status, got {ev['status']}"
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_m3_epoch_change_detection():
    """M3: Reader detects writer restart (epoch change) and yields EPOCH_CHANGE."""
    shm_name = "test_m3_epoch"
    writer1 = SHMWriter(name=shm_name, slot_count=128)
    reader = SHMReader(name=shm_name)
    try:
        writer1.write_tick(
            seq=0,
            symbol="BTC/USD",
            source="TEST",
            price=100.0,
            size=1.0,
            bid=99.0,
            ask=101.0,
            bid_size=1.0,
            ask_size=1.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=100.0,
            ingest_ts=100.0,
            broadcast_ts=100.0,
            engine_us=1.0,
        )
        # Writer restarts with a new epoch
        writer1.close()

        writer2 = SHMWriter(name=shm_name, slot_count=128)
        assert writer2.epoch_id != reader.epoch_id

        # Reader check_epoch_valid must report False
        assert not reader.check_epoch_valid()

        # Streaming should yield EPOCH_CHANGE
        stream_gen = reader.stream(timeout=0.2)
        item = next(stream_gen)
        assert item.get("type") == "EPOCH_CHANGE"
        writer2.close()
    finally:
        reader.close()
