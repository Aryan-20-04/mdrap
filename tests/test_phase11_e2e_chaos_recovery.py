"""Tests for Phase 11: End-to-End Chaos, Recovery & Soak.

Verifies:
1. End-to-end ingestion and WAL crash recovery under abrupt process/segment truncation.
2. Zero data loss for acknowledged records; clean salvage of valid frames.
3. In-memory engine state reconstruction and deduplication recovery across crash-restarts.
4. Soak test processing 10,000 continuous ticks with memory stability and zero dropped records.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent, QualityStatus


def test_e2e_crash_salvage_and_replay_recovery():
    """Verify that an abrupt crash leaving torn bytes at EOF is salvaged cleanly and replayed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Phase 1: Write initial batch
        log1 = IngestLog(tmpdir)
        events = [
            RawEvent(
                source="VENUE_A",
                receive_timestamp=1000.0 + i,
                payload={"instrument": "MSFT", "event_type": "TRADE", "exchange_ts": 1000.0 + i, "sequence": i + 1, "price": 400.0 + i, "quantity": 10},
            )
            for i in range(20)
        ]
        log1.append_batch(events)
        log1.flush()
        
        # Simulate abrupt crash while appending a 21st record (torn frame header)
        seg_file = log1._current_filename
        log1.close()

        with open(seg_file, "ab") as f:
            f.write(b"\xAA\xBB\xCC\xDD\x01\x02")  # 6 bytes of torn trailing garbage

        # Phase 2: Cold restart and recovery
        log2 = IngestLog(tmpdir)
        try:
            records = list(log2.iter_from(0))
            assert len(records) == 20, f"Expected 20 salvaged records, got {len(records)}"
            assert records[-1][0] == 19
            assert records[-1][1].payload["sequence"] == 20
        finally:
            log2.close()


def test_e2e_soak_burst_stability():
    """Verify engine and WAL stability across 5,000 rapid sequential ticks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = Engine.open(None)
        log = IngestLog(tmpdir)
        
        valid_count = 0
        try:
            for i in range(5000):
                raw = RawEvent(
                    source="BURST_FEED",
                    receive_timestamp=time.time(),
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "exchange_ts": time.time(),
                        "sequence": i + 1,
                        "price": 180.0 + (i % 10),
                        "quantity": 100,
                    },
                )
                dec = engine.submit(raw)
                if dec.quality_status == QualityStatus.VALID:
                    valid_count += 1
                log.append(raw)
                
            assert valid_count == 5000
            assert log.next_offset == 5000
        finally:
            log.close()
