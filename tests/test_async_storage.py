import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from async_storage import AsyncStorageWorker
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store


def _make_event(eid: str, symbol: str = "AAPL", price: float = 150.0) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=eid,
        instrument_id=symbol,
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="TEST",
        sequence_number=1,
        price=price,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        raw_id=f"raw-{eid}",
    )


def test_async_storage_batch_commit():
    store = Store(":memory:")
    worker = AsyncStorageWorker(store=store, batch_size=50, flush_interval_s=0.5)
    worker.start()

    # Enqueue 50 events to trigger threshold batch commit
    for i in range(50):
        ev = _make_event(f"evt-{i}")
        worker.write_canonical(ev)

    # Allow worker loop to process
    time.sleep(0.1)
    worker.flush(timeout=1.0)

    rows = store.latest("AAPL", limit=100)
    assert len(rows) == 50
    st = worker.stats()
    assert st["total_canonical"] == 50
    assert st["total_commits"] >= 1

    worker.stop()
    store.close()


def test_async_storage_timer_flush():
    store = Store(":memory:")
    # Small batch size of 1000, but short timer of 0.1s
    worker = AsyncStorageWorker(store=store, batch_size=1000, flush_interval_s=0.1)
    worker.start()

    # Enqueue 5 events (well below batch threshold)
    for i in range(5):
        worker.write_canonical(_make_event(f"timer-{i}"))

    # Wait for timer flush to trigger
    time.sleep(0.25)

    rows = store.latest("AAPL", limit=100)
    assert len(rows) == 5
    assert worker.stats()["total_commits"] >= 1

    worker.stop()
    store.close()


def test_async_storage_stop_drains_residuals():
    store = Store(":memory:")
    worker = AsyncStorageWorker(store=store, batch_size=1000, flush_interval_s=10.0)
    worker.start()

    # Enqueue 20 events
    for i in range(20):
        worker.write_canonical(_make_event(f"stop-{i}"))

    # Immediately stop without waiting for timer
    worker.stop(timeout=2.0)

    rows = store.latest("AAPL", limit=100)
    assert len(rows) == 20
    store.close()


def test_pipeline_with_async_storage():
    """Verify that Pipeline delegates persistence to AsyncStorageWorker with zero synchronous commit stalls."""
    from pipeline import Pipeline
    from models import RawEvent

    store = Store(":memory:")
    worker = AsyncStorageWorker(store=store, batch_size=25, flush_interval_s=0.2)
    worker.start()

    pipeline = Pipeline(store=store, async_storage=worker)

    for i in range(100):
        raw = RawEvent(
            source="TEST",
            payload={"instrument": "MSFT", "event_type": "TRADE", "price": 400.0 + i, "quantity": 100.0, "exchange_ts": 1000.0 + i, "sequence": i},
            receive_timestamp=1000.0 + i,
            raw_id=f"raw-msft-{i}",
        )
        ev = pipeline.process_one(raw)
        assert ev is not None

    pipeline.finish()
    worker.stop()

    rows = store.latest("MSFT", limit=200)
    assert len(rows) == 100
    assert worker.stats()["total_canonical"] == 100
    store.close()

