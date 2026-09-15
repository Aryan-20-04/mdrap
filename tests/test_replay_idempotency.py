"""
Test Replay Idempotency (Audit Invariant A2).

Verifies that Store write operations (canonical, quarantine, lineage) remain strictly
idempotent when identical events are replayed through the engine (e.g. from archive.py).
"""

from __future__ import annotations

import json
from models import CanonicalEvent, EventType, QualityStatus, Reason
from storage import Store


def test_canonical_write_idempotency():
    """Verify writing identical CanonicalEvents multiple times is idempotent."""
    events = [
        CanonicalEvent(
            event_id="test-evt-1",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=1000.0,
            receive_timestamp=1000.001,
            processing_timestamp=1000.002,
            source="FEEDX",
            sequence_number=1,
            price=150.25,
            quantity=100.0,
            quality_status=QualityStatus.VALID,
            reasons=[],
            raw_id="raw-1",
        ),
        CanonicalEvent(
            event_id="test-evt-2",
            instrument_id="MSFT",
            event_type=EventType.TRADE,
            exchange_timestamp=1000.1,
            receive_timestamp=1000.101,
            processing_timestamp=1000.102,
            source="FEEDY",
            sequence_number=2,
            price=310.50,
            quantity=50.0,
            quality_status=QualityStatus.VALID,
            reasons=[],
            raw_id="raw-2",
        ),
    ]

    with Store(":memory:") as store:
        # First write
        store.write_canonical_batch(events)
        store.commit()
        assert store.counts().get("VALID", 0) == 2

        # Second write (replay simulation)
        store.write_canonical_batch(events)
        store.commit()
        assert store.counts().get("VALID", 0) == 2

        rows = store.query_events(limit=10)
        assert len(rows) == 2
        assert {r["event_id"] for r in rows} == {"test-evt-1", "test-evt-2"}


def test_quarantine_and_lineage_idempotency():
    """Verify quarantine and lineage writes remain idempotent under replay."""
    quarantine_rows = [
        (
            "test-quar-1",
            "AAPL",
            "FEEDX",
            "INVALID",
            json.dumps([Reason.SCHEMA_VIOLATION.value]),
            json.dumps({"bad": "payload"}),
            1000.0,
        )
    ]
    lineage_rows = [
        (
            "test-evt-1",
            "AAPL",
            json.dumps(["raw-1"]),
            "raw-1",
            json.dumps(["ingest", "normalize"]),
            json.dumps(["price_sanity"]),
            0,
            "valid",
            "FEEDX",
            "v1",
            1000.002,
        )
    ]

    with Store(":memory:") as store:
        store.write_quarantine_batch(quarantine_rows)
        store.write_lineage_batch(lineage_rows)
        store.commit()

        q_count_1 = store.conn.execute("SELECT count(*) FROM quarantine").fetchone()[0]
        l_count_1 = store.conn.execute("SELECT count(*) FROM lineage").fetchone()[0]
        assert q_count_1 == 1
        assert l_count_1 == 1

        # Replay identical rows
        store.write_quarantine_batch(quarantine_rows)
        store.write_lineage_batch(lineage_rows)
        store.commit()

        q_count_2 = store.conn.execute("SELECT count(*) FROM quarantine").fetchone()[0]
        l_count_2 = store.conn.execute("SELECT count(*) FROM lineage").fetchone()[0]
        assert q_count_2 == 1
        assert l_count_2 == 1
