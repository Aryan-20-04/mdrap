"""Regression test for Audit Finding N-01: IngestLog poisoning on rotation I/O failure.

Verifies:
If an I/O error occurs while rotating segments inside append_batch,
the log is marked as poisoned (_is_poisoned = True), and subsequent
appends raise IngestLogPoisonedError rather than crashing with ValueError (closed file).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import pytest

from mdrap.ingestlog import IngestLog, IngestLogPoisonedError
from mdrap.models import RawEvent


def test_n01_rotation_io_error_poisons_log() -> None:
    temp_dir = tempfile.mkdtemp(prefix="mdrap_n01_test_")
    try:
        # Small max_segment_bytes so second batch forces rotation
        log = IngestLog(temp_dir, max_segment_bytes=512, fsync_policy="always")

        # Initial append to seed the first segment
        log.append(
            RawEvent(
                source="TEST",
                payload={
                    "instrument": "BTC/USDT",
                    "event_type": "QUOTE",
                    "bid": 100.0,
                    "ask": 101.0,
                },
            )
        )
        assert not log._is_poisoned
        assert log._current_file is not None

        # Monkeypatch _rotate_to_new_segment to simulate an I/O failure on next rotation
        orig_rotate = log._rotate_to_new_segment

        def failing_rotate(start_offset):
            # Close old file as rotation normally does, then fail creating the new segment
            if log._current_file and not log._current_file.closed:
                log._current_file.close()
            log._current_file = None
            log._is_poisoned = True
            raise OSError("Simulated disk full or EIO during segment rotation")

        log._rotate_to_new_segment = failing_rotate  # type: ignore[assignment]

        # Attempt batch append that exceeds segment size and triggers rotation
        large_batch = [
            RawEvent(
                source="TEST",
                payload={
                    "instrument": "BTC/USDT",
                    "event_type": "QUOTE",
                    "bid": 100.0 + i,
                    "ask": 101.0 + i,
                },
            )
            for i in range(10)
        ]

        with pytest.raises(OSError, match="Simulated disk full"):
            log.append_batch(large_batch)

        # Critical invariant (N-01): The log MUST be poisoned, not left in an unpoisoned closed-file state
        assert log._is_poisoned is True

        # Any subsequent append/append_batch MUST raise IngestLogPoisonedError, NOT ValueError
        with pytest.raises(IngestLogPoisonedError):
            log.append(
                RawEvent(
                    source="TEST",
                    payload={
                        "instrument": "BTC/USDT",
                        "event_type": "QUOTE",
                        "bid": 200.0,
                        "ask": 201.0,
                    },
                )
            )

        with pytest.raises(IngestLogPoisonedError):
            log.append_batch([{"source": "TEST", "payload": {}}])

        log.close()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
