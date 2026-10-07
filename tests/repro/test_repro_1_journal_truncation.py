"""Repro 1: Verify that an exception inside `with Pipeline(...)` does not blindly truncate the durability journal.

Expected behavior:
Uncommitted journal records must survive an unhandled exception inside the with-block
so they can be recovered by Store / projection restart.

Current defect:
Pipeline.__exit__() calls Pipeline.close(), which opens the journal in "w" mode,
truncating all unflushed journal data to 0 bytes.
"""

from __future__ import annotations

import os
import pytest

from mdrap.models import RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Pipeline.close() blindly truncates the durability journal on exception in with-block (Finding 1)",
)
def test_exception_in_pipeline_with_block_preserves_journal(tmp_path):
    db_path = str(tmp_path / "events.db")
    journal_path = f"{db_path}.journal"

    store = Store(db_path)
    try:
        # Long flush interval and async writer so events stay in-memory/journal
        with Pipeline(
            store=store,
            journal=journal_path,
            async_writer=True,
            flush_interval_s=3600.0,
        ) as pipeline:
            raw = RawEvent(
                source="REPRO_1",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 150.0,
                    "quantity": 10.0,
                    "exchange_ts": 1700000000.0,
                    "sequence": 1,
                },
            )
            ack = pipeline.process_one(raw)
            assert ack is not None

            # Verify event was written to journal before crash
            assert os.path.exists(journal_path)
            assert os.path.getsize(journal_path) > 0

            # Simulate unhandled exception / crash in caller code
            raise RuntimeError("Simulated caller failure inside Pipeline with-block")
    except RuntimeError:
        pass

    # The journal MUST NOT be truncated to 0 bytes when an exception occurred!
    assert os.path.exists(journal_path), "Journal file was deleted"
    journal_size = os.path.getsize(journal_path)
    assert journal_size > 0, "Journal was blindly truncated on exception, destroying uncommitted data!"

    # Furthermore, reopening the Store must recover the acknowledged event
    recovered_store = Store(db_path)
    count = recovered_store.conn.execute("SELECT count(*) FROM canonical_events").fetchone()[0]
    assert count == 1, "Failed to recover uncommitted event from surviving journal"
