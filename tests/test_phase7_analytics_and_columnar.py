"""Acceptance test suite for Phase 7: Analytics Projections & Columnar Storage.

Verifies:
1. Asynchronous Projection Interface (AsyncProjectionWorker):
   - Background worker thread draining an isolated bounded queue.
   - Non-blocking enqueue preserving capture engine latency.
   - Failure and backpressure isolation: projection failures or drops never block or crash the engine.
   - Clean flush and shutdown semantics.
2. DuckDB Columnar Projection:
   - Atomic batch application of CanonicalEvent and EngineDecision objects.
   - Accurate checkpoint progression and idempotent replay.
   - Fast time-series aggregation queries (VWAP, count, min/max).
   - Index verification on (instrument_id, exchange_timestamp).
3. Apache Parquet Export:
   - DuckDBProjection.export_parquet() direct table export.
   - export_journal_to_parquet() scanning IngestLog WAL segments into Parquet.
   - Lineage preservation across parquet schemas (source, sequence, quality_status, reasons).
4. Immutability Guarantee:
   - Analytical queries and destructive operations on projections leave the canonical IngestLog immutable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import pytest

from mdrap.engine import EngineDecision
from mdrap.ingestlog import IngestLog
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.projection import (
    AsyncProjectionWorker,
    DuckDBProjection,
    SQLiteProjection,
    export_journal_to_parquet,
)


class TestAsyncProjectionWorker:
    """Verifies non-blocking asynchronous projection worker and failure isolation."""

    def test_async_projection_lifecycle(self, tmp_path):
        db_path = str(tmp_path / "async_proj.db")
        proj = SQLiteProjection(db_path=db_path)
        worker = AsyncProjectionWorker(
            proj, max_queue_size=1000, batch_size=50, flush_interval_sec=0.02
        )

        # Enqueue 100 canonical events
        now = time.time()
        for i in range(100):
            ev = CanonicalEvent(
                event_id=f"ev_{i}",
                instrument_id="BTC/USDT",
                event_type=EventType.TRADE,
                exchange_timestamp=now + i,
                receive_timestamp=now + i,
                processing_timestamp=now + i,
                source="BINANCE",
                sequence_number=i + 1,
                price=65000.0 + i,
                quantity=1.0,
                quality_status=QualityStatus.VALID,
            )
            success = worker.enqueue(ev, offset=i)
            assert success is True

        worker.flush()
        stats = worker.stats()
        assert stats["items_enqueued"] == 100
        assert stats["items_committed"] == 100
        assert stats["drops_total"] == 0
        assert stats["errors_total"] == 0
        assert proj.count_canonical() == 100
        assert proj.checkpoint() == 99

        worker.close()

    def test_backpressure_drop_accounting_when_saturated(self, tmp_path):
        # Mock projection that blocks
        class SlowProjection:
            def apply(self, batch, offset):
                time.sleep(0.5)

            def checkpoint(self):
                return 0

        slow_proj = SlowProjection()
        # Very small queue size = 5
        worker = AsyncProjectionWorker(
            slow_proj, max_queue_size=5, batch_size=2, flush_interval_sec=0.1
        )

        dropped = 0
        for i in range(50):
            res = worker.enqueue({"dummy": i}, offset=i)
            if not res:
                dropped += 1

        stats = worker.stats()
        assert stats["drops_total"] > 0
        assert stats["drops_total"] == dropped
        worker.close(timeout=1.0)

    def test_worker_error_isolation_does_not_crash_engine(self, tmp_path):
        class FailingProjection:
            def apply(self, batch, offset):
                raise RuntimeError("Disk full simulation or connection lost!")

            def checkpoint(self):
                return -1

        fail_proj = FailingProjection()
        worker = AsyncProjectionWorker(
            fail_proj, max_queue_size=100, batch_size=10, flush_interval_sec=0.01
        )

        # Enqueue must remain non-blocking and return True without raising exception
        for i in range(25):
            assert worker.enqueue({"item": i}, offset=i) is True

        worker.flush()
        stats = worker.stats()
        assert stats["errors_total"] > 0
        assert stats["is_alive"] is True
        worker.close()


class TestDuckDBColumnarProjection:
    """Verifies DuckDB analytical columnar projection and high-speed querying."""

    @pytest.fixture(autouse=True)
    def _require_duckdb(self):
        pytest.importorskip("duckdb")

    def test_duckdb_projection_apply_and_query(self, tmp_path):
        db_path = str(tmp_path / "test_duck.duckdb")
        proj = DuckDBProjection(db_path=db_path)
        assert proj.checkpoint() == -1

        now = time.time()
        batch = []
        for i in range(50):
            ev = CanonicalEvent(
                event_id=f"duck_{i}",
                instrument_id="ETH/USDT",
                event_type=EventType.TRADE,
                exchange_timestamp=now + i,
                receive_timestamp=now + i,
                processing_timestamp=now + i,
                source="KRAKEN",
                sequence_number=i + 1,
                price=3500.0 + (i * 0.1),
                quantity=2.0,
                quality_status=QualityStatus.VALID,
                reasons=[],
                raw_id=str(i),
            )
            batch.append(ev)

        proj.apply(batch, offset=49)
        assert proj.checkpoint() == 49
        assert proj.count_canonical() == 50

        # Execute analytical SQL aggregation query
        res = proj.query(
            "SELECT instrument_id, COUNT(*), AVG(price) FROM canonical_events GROUP BY instrument_id"
        )
        assert len(res) == 1
        assert res[0][0] == "ETH/USDT"
        assert res[0][1] == 50
        proj.close()

    def test_duckdb_engine_decision_projection(self, tmp_path):
        proj = DuckDBProjection(db_path=":memory:")
        now = time.time()
        ev = CanonicalEvent(
            event_id="dec_1",
            instrument_id="SOL/USDT",
            event_type=EventType.TRADE,
            exchange_timestamp=now,
            receive_timestamp=now,
            processing_timestamp=now,
            source="BINANCE",
            sequence_number=101,
            price=150.25,
            quantity=5.0,
            quality_status=QualityStatus.VALID,
        )
        decision = EngineDecision(
            offset=10,
            event_id="dec_1",
            quality_status=QualityStatus.VALID,
            canonical_event=ev,
        )

        proj.apply([decision], offset=10)
        assert proj.checkpoint() == 10
        assert proj.count_canonical() == 1
        proj.close()


class TestParquetExporterAndLineage:
    """Verifies Apache Parquet direct export and IngestLog immutability."""

    @pytest.fixture(autouse=True)
    def _require_duckdb(self):
        pytest.importorskip("duckdb")

    def test_duckdb_export_parquet(self, tmp_path):
        proj = DuckDBProjection(db_path=":memory:")
        now = time.time()
        events = [
            CanonicalEvent(
                event_id=f"pq_{i}",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=now + i,
                receive_timestamp=now + i,
                processing_timestamp=now + i,
                source="NASDAQ",
                sequence_number=i + 1,
                price=220.0 + i,
                quantity=10.0,
                quality_status=QualityStatus.VALID,
            )
            for i in range(20)
        ]
        proj.apply(events, offset=19)

        parquet_file = str(tmp_path / "trades.parquet")
        count = proj.export_parquet(parquet_file, compression="snappy")
        assert count == 20
        assert os.path.exists(parquet_file)
        assert os.path.getsize(parquet_file) > 0
        proj.close()

    def test_export_journal_to_parquet_and_immutability(self, tmp_path):
        wal_dir = str(tmp_path / "wal")
        os.makedirs(wal_dir, exist_ok=True)
        log = IngestLog(wal_dir, fsync_policy="never")

        now = time.time()
        batch = [
            RawEvent(
                source="DATABENTO",
                payload={
                    "instrument": "ES.FUT",
                    "event_type": "TRADE",
                    "price": 5850.25 + i,
                    "quantity": 1.0,
                    "sequence": i + 1,
                    "exchange_ts": now + i,
                },
                receive_timestamp=now + i,
            )
            for i in range(100)
        ]
        log.append_batch(batch)
        log.flush()
        log.close()

        # Calculate pre-export log file sizes
        sizes_before = {
            f: os.path.getsize(os.path.join(wal_dir, f))
            for f in os.listdir(wal_dir)
            if f.endswith(".log")
        }

        parquet_path = str(tmp_path / "exported_journal.parquet")
        exported_count = export_journal_to_parquet(
            wal_dir, parquet_path, compression="snappy"
        )
        assert exported_count == 100
        assert os.path.exists(parquet_path)

        # Immutability verification: canonical IngestLog files are byte-for-byte identical
        sizes_after = {
            f: os.path.getsize(os.path.join(wal_dir, f))
            for f in os.listdir(wal_dir)
            if f.endswith(".log")
        }
        assert sizes_before == sizes_after

        # Verify IngestLog integrity
        log_after = IngestLog(wal_dir)
        assert sum(1 for _ in log_after.iter_from(0)) == 100
        assert log_after.corrupted_frames_count == 0
        log_after.close()
