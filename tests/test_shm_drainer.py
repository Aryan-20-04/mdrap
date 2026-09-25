"""
Tests for MDRAP Asynchronous Shared-Memory Drain Worker (Phase 4).
"""
import os
import tempfile
import time
import pytest

from shm import SHMWriter, HAS_SHM
from shm_drainer import SHMDrainWorker
from journal import BinaryJournalReader
from storage import Store


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_drainer_to_journal_and_store():
    shm_name = "test_shm_drainer_e2e"
    writer = SHMWriter(name=shm_name, slot_count=256)

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "drain.dbn")
        db_path = os.path.join(tmpdir, "drain.db")
        store = Store(db_path)

        drainer = SHMDrainWorker(
            shm_name=shm_name,
            journal_path=journal_path,
            store=store,
            batch_size=50,
            flush_interval_s=0.05,
        )
        drainer.start(start_seq=1)

        try:
            # Publisher produces 200 ticks into SHM ring
            for i in range(1, 201):
                writer.write_tick(
                    seq=i,
                    symbol="BTC/USD",
                    source="FEEDX",
                    price=60000.0 + i,
                    size=1.0,
                    bid=59999.0 + i,
                    ask=60001.0 + i,
                    bid_size=5.0,
                    ask_size=5.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0 + i,
                    ingest_ts=1000.0 + i + 0.0001,
                    broadcast_ts=1000.0 + i + 0.0002,
                    engine_us=10.0,
                )

            # Wait for drainer to capture all 200 events
            ok = drainer.drain_until(200, timeout=3.0)
            assert ok is True
            assert drainer.stats.drained_count == 200
            assert drainer.stats.last_drained_seq == 200

            stats_dict = drainer.stats.to_dict()
            assert stats_dict["drained_count"] == 200
            assert stats_dict["batches_flushed"] >= 1

        finally:
            drainer.close()
            writer.close()
            store.close()

        # Verify Journal contents
        with BinaryJournalReader(journal_path) as reader:
            assert len(reader) == 200
            rec1 = reader.read_record(0)
            assert rec1["seq"] == 1
            assert rec1["sym"] == "BTC/USD"
            assert rec1["price"] == pytest.approx(60001.0)

            rec200 = reader.read_record(199)
            assert rec200["seq"] == 200
            assert rec200["price"] == pytest.approx(60200.0)

        # Verify SQLite Store contents
        with Store(db_path) as verify_store:
            stored_events = verify_store.query_events("BTC/USD", limit=500)
            assert len(stored_events) == 200
            # query_events orders by exchange_timestamp DESC
            assert stored_events[0]["sequence_number"] == 200
            assert stored_events[-1]["sequence_number"] == 1


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_drainer_overrun_catchup():
    shm_name = "test_shm_drainer_overrun"
    writer = SHMWriter(name=shm_name, slot_count=64)

    try:
        # Pre-populate 150 events into 64-slot ring buffer
        for i in range(1, 151):
            writer.write_tick(
                seq=i,
                symbol="ETH/USD",
                source="COINBASE",
                price=3000.0 + i,
                size=2.0,
                bid=2999.0 + i,
                ask=3001.0 + i,
                bid_size=1.0,
                ask_size=1.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=1000.0 + i,
                ingest_ts=1000.0 + i,
                broadcast_ts=1000.0 + i,
                engine_us=5.0,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = os.path.join(tmpdir, "overrun_drain.dbn")
            with SHMDrainWorker(
                shm_name=shm_name,
                journal_path=journal_path,
                batch_size=32,
                flush_interval_s=0.02,
            ) as drainer:
                drainer.start(start_seq=1)
                ok = drainer.drain_until(150, timeout=3.0)
                assert ok is True
                assert drainer.stats.laps_detected >= 1
                assert drainer.stats.drained_count > 0
                assert drainer.stats.last_drained_seq == 150

            with BinaryJournalReader(journal_path) as reader:
                assert len(reader) > 0
                last_rec = reader.read_record(len(reader) - 1)
                assert last_rec["seq"] == 150

    finally:
        writer.close()
