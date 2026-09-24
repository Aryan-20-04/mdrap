import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shm import SHMWriter, SHMReader, HAS_SHM, SHM_FLAG_WATERMARK_WARNING
from watchdog import SourceWatchdog
from reconciliation import ReliabilityTracker


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_watermark_flag_set_and_clear():
    shm_name = "test_shm_wm_flag"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)
    try:
        assert not writer.is_watermark_warning_set()
        assert not reader.is_watermark_warning_set()

        writer.set_watermark_flag(True)
        assert writer.is_watermark_warning_set()
        assert reader.is_watermark_warning_set()

        writer.set_watermark_flag(False)
        assert not writer.is_watermark_warning_set()
        assert not reader.is_watermark_warning_set()
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_watermark_occupancy_trigger_on_writer():
    shm_name = "test_shm_wm_occ"
    writer = SHMWriter(name=shm_name, slot_count=16)
    reader = SHMReader(name=shm_name)
    try:
        # 16 slots * 0.80 = 12.8 -> 12 slots threshold
        assert writer.watermark_slots == 12

        # Write 13 ticks (seq 0 to 12)
        for seq in range(13):
            writer.write_tick(
                seq=seq,
                symbol="BTC/USD",
                source="SRC1",
                price=100.0 + seq,
                size=1.0,
                bid=99.0,
                ask=101.0,
                bid_size=10.0,
                ask_size=10.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=time.time(),
                ingest_ts=time.time(),
                broadcast_ts=time.time(),
                engine_us=1.0,
            )

        # Tell writer the reader is still at sequence 0
        writer.update_reader_seq(0)
        assert writer.is_watermark_warning_set()
        assert reader.is_watermark_warning_set()

        # Catch up the reader to sequence 12 (occupancy drops to 0)
        writer.update_reader_seq(12)
        assert not writer.is_watermark_warning_set()
        assert not reader.is_watermark_warning_set()
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_reader_watermark_detection_and_telemetry():
    shm_name = "test_shm_wm_reader"
    writer = SHMWriter(name=shm_name, slot_count=16)
    reader = SHMReader(name=shm_name)
    try:
        # Write 14 ticks: head becomes 14
        for seq in range(14):
            writer.write_tick(
                seq=seq,
                symbol="BTC/USD",
                source="SRC1",
                price=100.0 + seq,
                size=1.0,
                bid=99.0,
                ask=101.0,
                bid_size=10.0,
                ask_size=10.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=time.time(),
                ingest_ts=time.time(),
                broadcast_ts=time.time(),
                engine_us=1.0,
            )

        assert reader.check_watermark(current_seq=0) is True
        assert reader.check_watermark(current_seq=13) is False

        # Reading slot 0 has lag = 14 >= 12 -> triggers watermark warnings in overrun_stats
        item = reader.read_slot(0)
        assert item is not None
        assert reader.overrun_stats.watermark_warnings > 0
        assert reader.overrun_stats.watermark_events > 0

        # Reading slot 13 has lag = 1 < 12
        prev_warnings = reader.overrun_stats.watermark_warnings
        item = reader.read_slot(13)
        assert item is not None
        # When writer flag is false and lag < watermark, warning count does not increase
        assert reader.overrun_stats.watermark_warnings == prev_warnings
    finally:
        reader.close()
        writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_watchdog_watermark_alert_integration():
    shm_name = "test_shm_wm_wd"
    writer = SHMWriter(name=shm_name, slot_count=32)
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker)

    try:
        # Normal state: no watermark alert
        alert = watchdog.observe_shm_watermark(writer, source_name="SHM_FEED_1")
        assert alert is None
        assert len(watchdog.alerts()) == 0

        # High occupancy triggers watermark flag
        writer.set_watermark_flag(True)
        alert = watchdog.observe_shm_watermark(writer, source_name="SHM_FEED_1")
        assert alert is not None
        assert alert.alert_type == "WATERMARK_WARNING"
        assert alert.source == "SHM_FEED_1"
        assert "watermark crossed" in alert.details
        assert len(watchdog.alerts()) == 1
    finally:
        writer.close()
