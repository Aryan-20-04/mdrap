"""Repro 2: Verify that journal write failure fails closed by default and does not falsely acknowledge.

Expected behavior:
Under default durability policy (fail_closed):
- Journal write failure raises an exception or marks pipeline degraded
- Emits ERROR-level logging
- Event is NOT falsely acknowledged as durable

Current defect:
Pipeline._journal_append catches Exception, logs debug, and silently swallows it.
The event is falsely returned/acknowledged as if it were durably stored.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
import pytest

from mdrap.models import RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Pipeline._journal_append swallows journal write failure with logger.debug and falsely acks (Finding 2)",
)
def test_journal_write_failure_fails_closed_and_logs_error(tmp_path, caplog):
    db_path = str(tmp_path / "events.db")
    journal_path = f"{db_path}.journal"

    store = Store(db_path)
    pipeline = Pipeline(
        store=store,
        journal=journal_path,
        async_writer=False,
    )

    # Force journal write failure by mocking _journal_file.write to raise OSError
    broken_file = MagicMock()
    broken_file.write.side_effect = OSError("Disk I/O error or journal write failure")
    pipeline._journal_file = broken_file

    raw = RawEvent(
        source="REPRO_2",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "exchange_ts": 1700000000.0,
            "sequence": 1,
        },
    )

    with caplog.at_level(logging.ERROR):
        # Under fail_closed, this MUST raise an error and not falsely acknowledge!
        try:
            ack = pipeline.process_one(raw)
            # If it didn't raise, it must not return a successful acknowledgement
            assert ack is None, "Event was falsely acknowledged despite journal write failure"
        except Exception:
            # Expected to raise under fail_closed policy
            pass

        # An ERROR-level log record MUST be emitted when durability fails
        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_logs) > 0, "No ERROR-level log recorded for journal write failure"
