import os
import sys
import tempfile
import time
import pytest

from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus
from mdrap.quality import QualityEngine, QualityConfig
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


def test_batch_reorder_no_duplicate_dispatch():
    """Verify that batch processing does NOT dispatch held reorder events prematurely or twice (CORR-02)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        store = Store(db_path)
        # Configure pipeline with reorder buffer enabled
        qc = QualityConfig(reorder_window_s=0.5, reorder_max_slots=10)
        qe = QualityEngine(qc)
        pipeline = Pipeline(store=store, quality=qe)

        t_now = time.time()

        # Batch 1: Sequence 1 (in-order)
        raw1 = RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": t_now,
                "sequence": 1,
                "price": 150.0,
                "quantity": 10.0,
            },
            receive_timestamp=t_now + 0.001,
            raw_id="raw_1",
        )

        # Sequence 3 (jump! seq 2 is missing in-flight, should be held in reorder buffer)
        raw3 = RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": t_now + 0.002,
                "sequence": 3,
                "price": 150.2,
                "quantity": 30.0,
            },
            receive_timestamp=t_now + 0.003,
            raw_id="raw_3",
        )

        res_batch1 = pipeline.process_batch([raw1, raw3])

        # raw1 must be dispatched, but raw3 MUST NOT be dispatched yet (it is held in pending)
        assert len(res_batch1) == 1
        assert res_batch1[0].sequence_number == 1

        # Batch 2: Sequence 2 arrives (fills the gap)
        raw2 = RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": t_now + 0.0015,
                "sequence": 2,
                "price": 150.1,
                "quantity": 20.0,
            },
            receive_timestamp=t_now + 0.0025,
            raw_id="raw_2",
        )

        res_batch2 = pipeline.process_batch([raw2])

        # Batch 2 dispatches seq 2, and then drains the unlocked seq 3 in order!
        assert len(res_batch2) == 2
        assert res_batch2[0].sequence_number == 2
        assert res_batch2[1].sequence_number == 3

        # Force flush to SQLite store
        pipeline.flush()

        # Query all trades from storage: exactly 3 rows, no duplicate of seq 3!
        rows = store.query_events("AAPL")
        assert len(rows) == 3
        seqs = [r["sequence_number"] for r in rows]
        assert sorted(seqs) == [1, 2, 3], (
            f"Unexpected sequences or duplicates in store: {seqs}"
        )

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
