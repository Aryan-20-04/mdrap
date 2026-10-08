import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.storage import Store
from mdrap.pipeline import Pipeline, replay_dead_letter_spills, get_dead_letter_dir


def test_dead_letter_spill_on_storage_failure():
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    dl_dir = os.path.join(tmp_dir, "spills")
    os.environ["MDRAP_DEAD_LETTER_DIR"] = dl_dir

    try:
        store = Store(db_path)

        # Mock a failing store that raises on batch write
        class FailingStore(Store):
            def write_batches_atomic(self, *args, **kwargs):
                raise IOError("Simulated disk error during atomic batch write")

        fail_store = FailingStore(db_path)
        pipeline = Pipeline(fail_store, flush_interval_s=0.1, async_writer=True)

        ev = CanonicalEvent(
            event_id="evt-dl-1",
            instrument_id="AAPL",
            source="FEEDX",
            event_type=EventType.TRADE,
            exchange_timestamp=1000.0,
            receive_timestamp=1000.001,
            processing_timestamp=1000.002,
            sequence_number=1,
            price=150.0,
            quantity=100.0,
            quality_status=QualityStatus.VALID,
        )

        pipeline._enqueue_canonical(ev)
        with pytest.raises(IOError, match="Simulated disk error"):
            pipeline.flush(wait=True)

        # Verify a spill file was created in dl_dir
        assert os.path.exists(dl_dir)
        spill_files = [
            f
            for f in os.listdir(dl_dir)
            if f.startswith("spill-") and f.endswith(".jsonl")
        ]
        assert len(spill_files) == 1

        # Now test replaying into a working store!
        good_store = Store(db_path)
        stats = replay_dead_letter_spills(good_store, dead_letter_dir=dl_dir)
        assert stats["files_processed"] == 1
        assert stats["canonical_replayed"] == 1

        # Check that the event was actually written into good_store
        events = good_store.query_events("AAPL", limit=10)
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-dl-1"

        # Verify the file was marked as replayed
        replayed_files = [f for f in os.listdir(dl_dir) if f.endswith(".replayed")]
        assert len(replayed_files) == 1

        good_store.close()
        fail_store.close()
        store.close()
    finally:
        os.environ.pop("MDRAP_DEAD_LETTER_DIR", None)
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_supervisor_revives_dead_writer_thread():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        pipeline = Pipeline(store, flush_interval_s=1.0, async_writer=True)
        assert pipeline._writer_thread is not None
        assert pipeline._writer_thread.is_alive()

        old_thread = pipeline._writer_thread
        # Simulate writer thread crash by killing it or waiting for it to exit
        pipeline._writer_thread = None

        # Call flush() - supervisor should revive it
        pipeline.flush(wait=False)
        assert pipeline._writer_thread is not None
        assert pipeline._writer_thread.is_alive()
        assert pipeline._writer_thread is not old_thread

        pipeline.finish()
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
