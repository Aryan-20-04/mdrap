"""
Tests for Historical Storage Partitioner, Catalog, and Retention (src/historical.py).
"""

import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import time

import pytest
from models import CanonicalEvent, EventType, QualityStatus
from historical import (
    HistoricalPartitioner,
    HistoricalCatalog,
    RetentionPolicy,
    HAS_PYARROW,
)


@pytest.fixture
def temp_historical_dir():
    d = tempfile.mkdtemp(prefix="mdrap_test_hist_")
    yield d
    try:
        shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def test_partition_events_jsonl_gz(temp_historical_dir):
    """Verify partitioning events into jsonl.gz format with manifest updates."""
    partitioner = HistoricalPartitioner(base_dir=temp_historical_dir)

    # 2026-10-01 12:00:00 UTC = 1790856000.0
    ts1 = 1790856000.0
    # 2026-10-02 12:00:00 UTC = 1790942400.0
    ts2 = 1790942400.0

    events = [
        CanonicalEvent(
            event_id="ev-1",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=ts1,
            receive_timestamp=ts1 + 0.001,
            processing_timestamp=ts1 + 0.002,
            source="FEEDX",
            sequence_number=1,
            price=150.0,
            quantity=10.0,
        ),
        CanonicalEvent(
            event_id="ev-2",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=ts1 + 10.0,
            receive_timestamp=ts1 + 10.001,
            processing_timestamp=ts1 + 10.002,
            source="FEEDX",
            sequence_number=2,
            price=150.5,
            quantity=20.0,
        ),
        CanonicalEvent(
            event_id="ev-3",
            instrument_id="MSFT",
            event_type=EventType.TRADE,
            exchange_timestamp=ts2,
            receive_timestamp=ts2 + 0.001,
            processing_timestamp=ts2 + 0.002,
            source="FEEDY",
            sequence_number=1,
            price=310.0,
            quantity=15.0,
        ),
    ]

    res = partitioner.partition_events(events, chunk_size=10, fmt="jsonl.gz")
    assert res["rows_added"] == 3
    assert len(res["written_files"]) == 2  # 1 for AAPL day 1, 1 for MSFT day 2

    # Verify manifest
    catalog = HistoricalCatalog(base_dir=temp_historical_dir)
    assert catalog.manifest["total_rows"] == 3
    assert len(catalog.manifest["partitions"]) == 2

    # Query range AAPL
    aapl_rows = catalog.query_range(symbol="AAPL")
    assert len(aapl_rows) == 2
    assert {r["event_id"] for r in aapl_rows} == {"ev-1", "ev-2"}

    # Query range MSFT
    msft_rows = catalog.query_range(symbol="MSFT")
    assert len(msft_rows) == 1
    assert msft_rows[0]["event_id"] == "ev-3"


def test_partition_events_csv_gz(temp_historical_dir):
    """Verify partitioning events into csv.gz format and reading with column filtering."""
    partitioner = HistoricalPartitioner(base_dir=temp_historical_dir)
    ts = 1790856000.0

    events = [
        CanonicalEvent(
            event_id="ev-1",
            instrument_id="NVDA",
            event_type=EventType.TRADE,
            exchange_timestamp=ts,
            receive_timestamp=ts,
            processing_timestamp=ts,
            source="FEEDX",
            sequence_number=1,
            price=450.0,
            quantity=100.0,
        )
    ]

    partitioner.partition_events(events, fmt="csv.gz")

    catalog = HistoricalCatalog(base_dir=temp_historical_dir)
    rows = catalog.query_range(symbol="NVDA", columns=["event_id", "price"])
    assert len(rows) == 1
    assert rows[0]["event_id"] == "ev-1"
    assert float(rows[0]["price"]) == 450.0
    assert "source" not in rows[0]


def test_partition_from_sqlite(temp_historical_dir):
    """Verify streaming partition from an SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE canonical_events (
                event_id TEXT PRIMARY KEY,
                instrument_id TEXT,
                event_type TEXT,
                exchange_timestamp REAL,
                receive_timestamp REAL,
                processing_timestamp REAL,
                source TEXT,
                sequence_number INTEGER,
                price REAL,
                quantity REAL,
                bid_price REAL,
                bid_size REAL,
                ask_price REAL,
                ask_size REAL,
                quality_status TEXT,
                reasons TEXT,
                raw_id TEXT
            )
        """)
        ts = 1790856000.0
        conn.execute("""
            INSERT INTO canonical_events VALUES
            ('e1', 'GOOG', 'TRADE', ?, ?, ?, 'SRC', 1, 140.0, 50.0, NULL, NULL, NULL, NULL, 'VALID', '[]', 'r1'),
            ('e2', 'GOOG', 'TRADE', ?, ?, ?, 'SRC', 2, 141.0, 60.0, NULL, NULL, NULL, NULL, 'VALID', '[]', 'r2')
        """, (ts, ts, ts, ts + 1, ts + 1, ts + 1))
        conn.commit()
        conn.close()

        partitioner = HistoricalPartitioner(base_dir=temp_historical_dir)
        stats = partitioner.partition_from_sqlite(db_path, fmt="jsonl.gz")
        assert stats["rows_added"] == 2

        catalog = HistoricalCatalog(base_dir=temp_historical_dir)
        goog_rows = catalog.query_range(symbol="GOOG")
        assert len(goog_rows) == 2
        assert [r["event_id"] for r in goog_rows] == ["e1", "e2"]
    finally:
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_retention_policy_purging(temp_historical_dir):
    """Verify retention policy identifies and removes expired partitions."""
    partitioner = HistoricalPartitioner(base_dir=temp_historical_dir)

    now = time.time()
    old_ts = now - (40 * 86400.0)  # 40 days ago
    recent_ts = now - (2 * 86400.0)  # 2 days ago

    old_event = CanonicalEvent(
        event_id="old-1",
        instrument_id="SPY",
        event_type=EventType.TRADE,
        exchange_timestamp=old_ts,
        receive_timestamp=old_ts,
        processing_timestamp=old_ts,
        source="FEED",
        sequence_number=1,
        price=400.0,
    )
    recent_event = CanonicalEvent(
        event_id="recent-1",
        instrument_id="SPY",
        event_type=EventType.TRADE,
        exchange_timestamp=recent_ts,
        receive_timestamp=recent_ts,
        processing_timestamp=recent_ts,
        source="FEED",
        sequence_number=2,
        price=500.0,
    )

    partitioner.partition_events([old_event, recent_event], fmt="jsonl.gz")
    catalog_before = HistoricalCatalog(base_dir=temp_historical_dir)
    assert catalog_before.manifest["total_rows"] == 2

    # Apply 30-day retention
    retention = RetentionPolicy(base_dir=temp_historical_dir)
    res = retention.apply_retention(max_age_days=30, dry_run=False)

    assert res["pruned_partitions"] == 1
    assert res["deleted_files"] == 1
    assert res["rows_removed"] == 1

    catalog_after = HistoricalCatalog(base_dir=temp_historical_dir)
    assert catalog_after.manifest["total_rows"] == 1

    remaining = catalog_after.query_range(symbol="SPY")
    assert len(remaining) == 1
    assert remaining[0]["event_id"] == "recent-1"
