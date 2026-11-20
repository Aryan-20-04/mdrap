"""Tests for multithreaded concurrent SQLite read synchronization (MDRAP-PERS-02).

Verifies:
1. All read queries on MetadataStore / Store using read_conn are protected by _read_lock.
2. Concurrent readers cannot trigger sqlite3 cursor collisions (sqlite3.ProgrammingError).
3. Concurrent reads while background writes occur maintain full data integrity.
"""

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.storage import Store


def _make_event(i: int, inst: str = "BTC-USD") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"ev_sync_{i}",
        instrument_id=inst,
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0 + i,
        receive_timestamp=1000.0 + i,
        source="BINANCE",
        price=50000.0 + (i % 100),
        quantity=1.0,
        quality_status=QualityStatus.VALID,
    )


def test_concurrent_sqlite_readers_no_cursor_collision():
    """Verify concurrent reads on shared read_conn do not cause cursor collisions (MDRAP-PERS-02)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_sync.db")
        store = Store(db_path)
        try:
            # Seed initial records
            events = [_make_event(i) for i in range(200)]
            store.write_canonical_batch(events)
            store.commit()

            errors: list[Exception] = []
            stop_event = threading.Event()

            def reader_worker(worker_id: int):
                while not stop_event.is_set():
                    try:
                        max_row = store.get_max_rowid("canonical_events")
                        assert max_row > 0
                        recs = store.query_canonical_after_rowid(max(0, max_row - 10), limit=10)
                        assert isinstance(recs, list)
                        events = store.query_events("BTC-USD", limit=5)
                        assert isinstance(events, list)
                        c = store.counts()
                        assert isinstance(c, dict)
                    except Exception as exc:
                        errors.append(exc)
                        break

            def writer_worker():
                counter = 1000
                while not stop_event.is_set():
                    try:
                        evs = [_make_event(counter + j) for j in range(10)]
                        store.write_canonical_batch(evs)
                        store.commit()
                        counter += 10
                        time.sleep(0.01)
                    except Exception as exc:
                        errors.append(exc)
                        break

            # Launch 8 concurrent reader threads and 2 writer threads
            threads = []
            for r in range(8):
                t = threading.Thread(target=reader_worker, args=(r,))
                threads.append(t)
                t.start()

            for w in range(2):
                t = threading.Thread(target=writer_worker)
                threads.append(t)
                t.start()

            # Let them hammer concurrent queries and writes for 1.5 seconds
            time.sleep(1.5)
            stop_event.set()

            for t in threads:
                t.join(timeout=3.0)

            # Invariant: ZERO cursor collisions or ProgrammingError
            assert len(errors) == 0, f"Concurrent SQLite reader/writer encountered errors: {errors}"
        finally:
            store.close()


def test_bounded_reader_pool_parallel_reads_and_thread_churn(tmp_path):
    db_path = os.path.join(tmp_path, "bounded_readers.db")
    store = Store(db_path)
    store.write_canonical_batch([_make_event(i) for i in range(200)])
    store.commit()

    try:
        for reader_count in (1, 2, 4, 8, 16):
            barrier = threading.Barrier(reader_count)

            def reader():
                barrier.wait(timeout=5)
                for _ in range(10):
                    assert len(store.query_events("BTC-USD", limit=5)) == 5

            with ThreadPoolExecutor(max_workers=reader_count) as pool:
                list(pool.map(lambda _: reader(), range(reader_count)))
            stats = store.reader_pool_stats
            assert stats["connections"] <= stats["limit"] == 16
            assert stats["active"] == 0
            assert stats["page_cache_limit_mb"] <= 32
            assert stats["mmap_virtual_limit_mb"] <= 256

        # Repeated short-lived workers must reuse bounded idle connections.
        for _ in range(8):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: store.query_events("BTC-USD", limit=1), range(8)))
        stats = store.reader_pool_stats
        assert stats["connections"] <= 16
        assert stats["active"] == 0
    finally:
        store.close()
    assert store.reader_pool_stats["connections"] == 0
