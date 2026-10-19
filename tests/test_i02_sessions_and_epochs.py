"""Acceptance tests for MDRAP Program I-2: Sessions and Sequence Epochs.

Verifies:
1. Strict policy flags SOD sequence resets as SUSPICIOUS/OUT_OF_ORDER (failsafe DMA baseline).
2. session_marker policy cleanly advances epoch on session_id change or is_reset=True, achieving 0 false alarms.
3. auto_detect policy detects sequence drops with exchange time jumps (e.g. overnight), achieving 0 false alarms.
4. QualityEngine directly coordinates session/epoch boundaries.
5. Snapshot and restore correctly serialize and deserialize epoch coordinates across sessions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from mdrap.clock import FixedClock
from mdrap.config import QualityConfig
from mdrap.engine import Engine, EngineConfig
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from mdrap.quality import QualityEngine


def test_i02_strict_policy_flags_sequence_resets() -> None:
    """Under strict policy, exchange SOD sequence reset triggers OUT_OF_ORDER alarms."""
    clock = FixedClock(1700000000.0)
    engine = Engine(staleness_threshold_s=10.0, clock=clock, session_policy="strict")

    # Day 1: 200 sequential events
    day1_events = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0 + i * 0.01,
                "quantity": 100.0,
                "sequence": i,
                "exchange_ts": 1700000000.0 + i,
            },
            receive_timestamp=1700000000.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d1 = engine.submit(day1_events)
    assert len(decisions_d1) == 200
    for d in decisions_d1:
        assert d.quality_status == QualityStatus.VALID

    # Day 2: Sequence resets to 1 after overnight gap (+86400s)
    day2_events = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 152.0 + i * 0.01,
                "quantity": 100.0,
                "sequence": i,
                "exchange_ts": 1700086400.0 + i,
            },
            receive_timestamp=1700086400.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d2 = engine.submit(day2_events)
    assert len(decisions_d2) == 200

    # In strict mode, event 1 through 199 on Day 2 are < 200, so they trigger OUT_OF_ORDER
    out_of_order_count = sum(
        1 for d in decisions_d2 if Reason.OUT_OF_ORDER.value in d.reasons
    )
    assert out_of_order_count == 199
    # Event 200 matches or exceeds last_seq (which was 200), so it's a DUPLICATE (200 == 200)
    assert decisions_d2[-1].quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in decisions_d2[-1].reasons


def test_i02_session_marker_policy_zero_false_alarms() -> None:
    """Under session_marker policy, explicit session marker or is_reset advances epoch with zero false alarms."""
    clock = FixedClock(1700000000.0)
    engine = Engine(
        staleness_threshold_s=10.0, clock=clock, session_policy="session_marker"
    )

    # Day 1: Session 2026-10-18, seq 1..200
    day1_events = [
        RawEvent(
            source="NASDAQ",
            session_id="SESSION_2026_10_18",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0 + i * 0.01,
                "quantity": 100.0,
                "sequence": i,
                "exchange_ts": 1700000000.0 + i,
            },
            receive_timestamp=1700000000.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d1 = engine.submit(day1_events)
    assert len(decisions_d1) == 200
    for d in decisions_d1:
        assert d.quality_status == QualityStatus.VALID

    # Day 2: Session 2026-10-19, seq 1..200
    day2_events = [
        RawEvent(
            source="NASDAQ",
            session_id="SESSION_2026_10_19",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 152.0 + i * 0.01,
                "quantity": 100.0,
                "sequence": i,
                "exchange_ts": 1700086400.0 + i,
            },
            receive_timestamp=1700086400.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d2 = engine.submit(day2_events)
    assert len(decisions_d2) == 200

    # First event of Day 2 is tagged with Reason.EPOCH_RESET and is VALID
    d2_first = decisions_d2[0]
    assert Reason.EPOCH_RESET.value in d2_first.reasons
    assert d2_first.quality_status == QualityStatus.VALID
    assert d2_first.canonical_event is not None
    assert d2_first.canonical_event.session_id == "SESSION_2026_10_19"

    # Every single event in Day 2 is VALID; exactly 0 OUT_OF_ORDER, 0 DUPLICATE, 0 SEQUENCE_GAP
    for d in decisions_d2:
        assert d.quality_status == QualityStatus.VALID
        assert Reason.OUT_OF_ORDER.value not in d.reasons
        assert Reason.DUPLICATE.value not in d.reasons
        assert Reason.SEQUENCE_GAP.value not in d.reasons

    # Total engine counts: 400 processed, 400 VALID, 0 SUSPICIOUS, 0 INVALID
    metrics = engine.metrics()
    assert metrics["processed"] == 400
    assert metrics["valid"] == 400
    assert metrics["suspicious"] == 0
    assert metrics["invalid"] == 0


def test_i02_auto_detect_policy_overnight_gap() -> None:
    """Under auto_detect policy, sequence drops paired with time gaps advance epoch automatically."""
    clock = FixedClock(1700000000.0)
    engine = Engine(
        staleness_threshold_s=10.0, clock=clock, session_policy="auto_detect"
    )

    # Day 1: seq 1..200
    day1_events = [
        RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5000.0 + i * 0.25,
                "quantity": 10.0,
                "sequence": i,
                "exchange_ts": 1700000000.0 + i,
            },
            receive_timestamp=1700000000.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d1 = engine.submit(day1_events)
    assert len(decisions_d1) == 200
    for d in decisions_d1:
        assert d.quality_status == QualityStatus.VALID

    # Day 2: seq 1..200 at exch_ts +86400s (overnight)
    day2_events = [
        RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5050.0 + i * 0.25,
                "quantity": 10.0,
                "sequence": i,
                "exchange_ts": 1700086400.0 + i,
            },
            receive_timestamp=1700086400.0 + i,
        )
        for i in range(1, 201)
    ]
    decisions_d2 = engine.submit(day2_events)
    assert len(decisions_d2) == 200

    # First event of Day 2 triggers EPOCH_RESET via auto_detect
    assert Reason.EPOCH_RESET.value in decisions_d2[0].reasons
    assert decisions_d2[0].quality_status == QualityStatus.VALID

    # All events on Day 2 are VALID with zero false alarms
    for d in decisions_d2:
        assert d.quality_status == QualityStatus.VALID
        assert Reason.OUT_OF_ORDER.value not in d.reasons
        assert Reason.DUPLICATE.value not in d.reasons

    assert engine.state.counts["VALID"] == 400
    assert engine.state.counts["SUSPICIOUS"] == 0
    assert engine.state.counts["INVALID"] == 0


def test_i02_quality_evaluator_epoch_and_session() -> None:
    """Test QualityEngine directly with session markers and auto-detect policies."""
    cfg = QualityConfig(session_policy="session_marker", staleness_threshold_s=10.0)
    evaluator = QualityEngine(cfg)

    # Day 1 tick
    ev1 = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.0,
        source="NASDAQ",
        sequence_number=100,
        price=150.0,
        quantity=10.0,
        session_id="SESS_DAY_1",
    )
    evaluator.evaluate(ev1)
    assert ev1.quality_status == QualityStatus.VALID

    # Day 2 tick with sequence reset to 1 and new session_id
    ev2 = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1700086400.0,
        receive_timestamp=1700086400.0,
        source="NASDAQ",
        sequence_number=1,
        price=151.0,
        quantity=10.0,
        session_id="SESS_DAY_2",
    )
    evaluator.evaluate(ev2)
    assert ev2.quality_status == QualityStatus.VALID
    assert Reason.EPOCH_RESET.value in ev2.reasons
    assert Reason.OUT_OF_ORDER.value not in ev2.reasons


def test_i02_snapshot_restore_preserves_epochs() -> None:
    """Snapshot and restore correctly persists epoch coordinates and seamlessly continues stream."""
    clock = FixedClock(1700000000.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal"
        snap_path = Path(tmpdir) / "snapshot.json"

        cfg = EngineConfig(
            wal_dir=str(wal_path),
            session_policy="session_marker",
            staleness_threshold_s=10.0,
            clock=clock,
        )
        engine1 = Engine(cfg)

        # Day 1: 50 events
        d1 = [
            RawEvent(
                source="BATS",
                session_id="DAY1",
                payload={
                    "instrument": "MSFT",
                    "event_type": "TRADE",
                    "price": 300.0 + i,
                    "quantity": 10.0,
                    "sequence": i,
                    "exchange_ts": 1700000000.0 + i,
                },
                receive_timestamp=1700000000.0 + i,
            )
            for i in range(1, 51)
        ]
        engine1.submit(d1)

        # Day 2: 20 events (epoch reset triggers on first)
        d2 = [
            RawEvent(
                source="BATS",
                session_id="DAY2",
                payload={
                    "instrument": "MSFT",
                    "event_type": "TRADE",
                    "price": 310.0 + i,
                    "quantity": 10.0,
                    "sequence": i,
                    "exchange_ts": 1700086400.0 + i,
                },
                receive_timestamp=1700086400.0 + i,
            )
            for i in range(1, 21)
        ]
        engine1.submit(d2)

        # Snapshot at Day 2 seq 20
        engine1.snapshot(snap_path)
        engine1.close()

        # Restore in engine2
        engine2 = Engine.restore(snap_path, config=cfg)

        # State verification
        assert engine2.state.epoch_state.get("BATS:MSFT") == 1
        assert engine2.state.session_state.get("BATS:MSFT") == "DAY2"

        # Submit next event in Day 2 (seq 21)
        next_event = RawEvent(
            source="BATS",
            session_id="DAY2",
            payload={
                "instrument": "MSFT",
                "event_type": "TRADE",
                "price": 331.0,
                "quantity": 10.0,
                "sequence": 21,
                "exchange_ts": 1700086421.0,
            },
            receive_timestamp=1700086421.0,
        )
        dec = engine2.submit(next_event)
        assert dec.quality_status == QualityStatus.VALID
        assert Reason.OUT_OF_ORDER.value not in dec.reasons
        assert Reason.EPOCH_RESET.value not in dec.reasons  # Within same epoch!
        engine2.close()
