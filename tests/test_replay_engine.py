"""
Tests for Deterministic Historical Replay Engine (src/replay.py).
"""

import os
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock

import pytest
from models import CanonicalEvent, EventType, QualityStatus
from journal import BinaryJournal
from replay import (
    VirtualClock,
    ReplayPacer,
    HistoricalReplayEngine,
    ReplayStats,
)


def test_virtual_clock_advancement():
    """Verify virtual clock starts at initial time and advances monotonically."""
    clock = VirtualClock(1000.0)
    assert clock.now == 1000.0

    clock.advance(0.5)
    assert clock.now == 1000.5

    clock.set_time(2000.0)
    assert clock.now == 2000.0

    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_virtual_clock_override_context():
    """Verify virtual clock context manager overrides time.time()."""
    clock = VirtualClock(1600000000.0)
    real_time_before = time.time()
    assert real_time_before > 1700000000.0

    with clock.override_time():
        assert time.time() == 1600000000.0
        clock.advance(10.0)
        assert time.time() == 1600000010.0

    # Ensure time.time() is properly restored
    assert time.time() > 1700000000.0


def test_replay_pacer_unthrottled():
    """Verify unthrottled pacing (speed_factor=None or 0) introduces zero sleep."""
    pacer = ReplayPacer(speed_factor=None)
    slept = pacer.pace(100.0)
    assert slept == 0.0
    slept = pacer.pace(105.0)
    assert slept == 0.0


def test_replay_pacer_pacing_accuracy():
    """Verify pacing introduces appropriate delay proportional to delta and speed_factor."""
    # 10x speed multiplier: 0.05s virtual delta should take ~0.005s wall time
    pacer = ReplayPacer(speed_factor=10.0)
    t0 = time.perf_counter()
    pacer.pace(100.0)
    slept = pacer.pace(100.05)
    wall_duration = time.perf_counter() - t0

    assert slept > 0.002
    assert wall_duration >= 0.003
    assert pacer.avg_drift_us >= 0.0


def test_replay_engine_stream_deterministic_order():
    """Verify replay stream strictly sorts events by (exchange_ts, seq, source, event_id)."""
    # Create intentionally out-of-order events
    ev1 = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.2,
        receive_timestamp=100.2,
        processing_timestamp=100.2,
        source="FEED_B",
        sequence_number=2,
        price=150.0,
    )
    ev2 = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.1,
        receive_timestamp=100.1,
        processing_timestamp=100.1,
        source="FEED_A",
        sequence_number=1,
        price=149.5,
    )
    ev3 = CanonicalEvent(
        event_id="e3",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.3,
        receive_timestamp=100.3,
        processing_timestamp=100.3,
        source="FEED_A",
        sequence_number=3,
        price=150.5,
    )

    engine = HistoricalReplayEngine(speed_factor=None, sync_virtual_clock=True)
    events_out = []

    gen = engine.stream([ev1, ev3, ev2], pacing=False)
    try:
        while True:
            events_out.append(next(gen))
    except StopIteration as exc:
        stats: ReplayStats = exc.value

    assert len(events_out) == 3
    assert [e.event_id for e in events_out] == ["e1", "e2", "e3"]
    assert all(e.source_kind == "REPLAY" for e in events_out)
    assert engine.virtual_clock.now == 100.3
    assert stats.events_replayed == 3
    assert stats.total_events == 3


def test_replay_engine_pause_and_stop():
    """Verify pause and early stop controls on replay stream."""
    events = [
        CanonicalEvent(
            event_id=f"e{i}",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=100.0 + i,
            receive_timestamp=100.0 + i,
            processing_timestamp=100.0 + i,
            source="FEED_A",
            sequence_number=i,
            price=150.0 + i,
        )
        for i in range(10)
    ]

    engine = HistoricalReplayEngine(speed_factor=None)
    gen = engine.stream(events, pacing=False)

    received = []
    received.append(next(gen))
    received.append(next(gen))
    assert len(received) == 2

    # Stop engine
    engine.stop()
    with pytest.raises(StopIteration):
        next(gen)


def test_replay_engine_load_from_sqlite():
    """Verify loading and replay from SQLite canonical_events table."""
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
        conn.execute("""
            INSERT INTO canonical_events VALUES
            ('ev-1', 'AAPL', 'TRADE', 1000.0, 1000.01, 1000.02, 'FEED1', 1, 150.0, 10.0, NULL, NULL, NULL, NULL, 'VALID', '[]', 'raw-1'),
            ('ev-2', 'MSFT', 'TRADE', 1001.0, 1001.01, 1001.02, 'FEED2', 2, 300.0, 20.0, NULL, NULL, NULL, NULL, 'VALID', '[]', 'raw-2')
        """)
        conn.commit()
        conn.close()

        engine = HistoricalReplayEngine(speed_factor=None)
        loaded = engine.load_from_sqlite(db_path, symbol="AAPL")
        assert len(loaded) == 1
        assert loaded[0].event_id == "ev-1"
        assert loaded[0].instrument_id == "AAPL"

        all_loaded = engine.load_from_sqlite(db_path)
        assert len(all_loaded) == 2
        assert all_loaded[0].event_id == "ev-1"
        assert all_loaded[1].event_id == "ev-2"
    finally:
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_replay_engine_load_from_binary_journal():
    """Verify loading and replay from append-only binary journal."""
    with tempfile.NamedTemporaryFile(suffix=".dbn", delete=False) as tf:
        jnl_path = tf.name

    try:
        with BinaryJournal(jnl_path, initial_records=16) as jnl:
            jnl.append_tick(seq=1, symbol="AAPL", source="FEEDX", price=150.25, size=100.0, exchange_ts=500.0)
            jnl.append_tick(seq=2, symbol="AAPL", source="FEEDX", price=150.50, size=200.0, exchange_ts=501.0)
            jnl.flush()

        engine = HistoricalReplayEngine(speed_factor=None)
        loaded = engine.load_from_journal(jnl_path)
        assert len(loaded) == 2
        assert loaded[0].sequence_number == 1
        assert loaded[0].price == 150.25
        assert loaded[1].sequence_number == 2
        assert loaded[1].price == 150.50
    finally:
        try:
            os.remove(jnl_path)
        except OSError:
            pass


def test_replay_to_pipeline_dispatch():
    """Verify replay_to_pipeline dispatches events directly to pipeline."""
    mock_pipeline = MagicMock()
    events = [
        CanonicalEvent(
            event_id=f"e{i}",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=100.0 + i,
            receive_timestamp=100.0 + i,
            processing_timestamp=100.0 + i,
            source="FEED_A",
            sequence_number=i,
            price=150.0,
        )
        for i in range(5)
    ]

    engine = HistoricalReplayEngine(speed_factor=None)
    stats = engine.replay_to_pipeline(mock_pipeline, events, pacing=False)

    assert stats.events_replayed == 5
    assert mock_pipeline.process_canonical.call_count == 5
