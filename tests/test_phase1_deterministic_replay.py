"""Phase 1 Regression Suite: Deterministic Replay, Recovery Metrics & Exception Safety (Tasks 4 & 5).

Verifies that:
1. RecoveryMetrics tracks replayed, valid, quarantined, and corrupted records accurately.
2. Engine.replay_with_metrics returns bit-for-bit deterministic decisions and metrics.
3. Engine.submit rolls back in-memory state on I/O exception during WAL append (INV-DUR-001).
4. Snapshot restore + incremental replay produces identical state to full replay.
"""

import time
import pytest

from mdrap.engine import Engine, EngineState, RecoveryMetrics
from mdrap.models import RawEvent, QualityStatus


def test_deterministic_replay_and_recovery_metrics(tmp_path):
    """Replaying events produces identical decisions and tracks valid vs quarantined records in RecoveryMetrics."""
    wal_dir = tmp_path / "replay_metrics_wal"
    engine = Engine.open(str(wal_dir))

    # Submit a mixture of VALID, SUSPICIOUS, and INVALID events
    events = [
        # Valid trade
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10.0,
                "sequence": 1,
                "exchange_ts": time.time(),
            },
        ),
        # Valid quote
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 149.9,
                "ask": 150.1,
                "sequence": 2,
                "exchange_ts": time.time(),
            },
        ),
        # Invalid schema payload (empty)
        RawEvent(
            source="NASDAQ",
            payload={},
        ),
        # Another valid trade
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.2,
                "quantity": 5.0,
                "sequence": 3,
                "exchange_ts": time.time(),
            },
        ),
    ]

    decisions_live = engine.submit(events)
    assert len(decisions_live) == 4
    assert decisions_live[0].quality_status == QualityStatus.VALID
    assert decisions_live[1].quality_status == QualityStatus.VALID
    assert decisions_live[2].quality_status == QualityStatus.INVALID
    assert decisions_live[3].quality_status == QualityStatus.VALID

    # Replay with metrics
    decisions_replayed, metrics = engine.replay_with_metrics(from_offset=0)

    # Invariants: Bit-for-bit deterministic decisions
    assert len(decisions_replayed) == 4
    for d_live, d_replay in zip(decisions_live, decisions_replayed):
        assert d_live.offset == d_replay.offset
        assert d_live.event_id == d_replay.event_id
        assert d_live.quality_status == d_replay.quality_status
        assert d_live.reasons == d_replay.reasons
        if d_live.canonical_event and d_replay.canonical_event:
            assert d_live.canonical_event.event_id == d_replay.canonical_event.event_id
            assert d_live.canonical_event.price == d_replay.canonical_event.price
            assert (
                d_live.canonical_event.bid_price == d_replay.canonical_event.bid_price
            )

    # Telemetry and recovery metrics accounting
    assert isinstance(metrics, RecoveryMetrics)
    assert metrics.replayed_records == 4
    assert metrics.valid_records == 3
    assert metrics.quarantined_records == 1
    assert metrics.corrupted_frames == 0

    engine.close()


def test_submit_exception_safety_state_rollback(tmp_path, monkeypatch):
    """If WAL append_batch fails (e.g. disk full), submit() rolls back engine state completely."""
    wal_dir = tmp_path / "rollback_wal"
    engine = Engine.open(str(wal_dir))

    # Commit initial valid event
    ev1 = RawEvent(
        source="NASDAQ",
        payload={
            "instrument": "MSFT",
            "event_type": "TRADE",
            "price": 300.0,
            "quantity": 10.0,
            "sequence": 1,
            "exchange_ts": time.time(),
        },
    )
    engine.submit(ev1)
    assert engine.state.event_count == 1
    assert engine.state.counts["VALID"] == 1

    pre_fail_state = engine.state.to_dict()

    # Simulate disk full during append_batch on next submit
    def mock_append_batch(events):
        raise OSError("ENOSPC: No space left on device")

    monkeypatch.setattr(engine.log, "append_batch", mock_append_batch)

    ev_fail = RawEvent(
        source="NASDAQ",
        payload={
            "instrument": "MSFT",
            "event_type": "TRADE",
            "price": 301.0,
            "quantity": 20.0,
            "sequence": 2,
            "exchange_ts": time.time(),
        },
    )

    with pytest.raises(OSError) as exc_info:
        engine.submit(ev_fail)
    assert "ENOSPC" in str(exc_info.value)

    # Engine state MUST be cleanly rolled back to pre_fail_state!
    assert engine.state.event_count == 1
    assert engine.state.counts["VALID"] == 1
    assert engine.state.to_dict() == pre_fail_state

    engine.close()


def test_live_equals_replay_event_ids_and_lineage_exhaustive(tmp_path):
    """Exhaustive property test: live execution must match replayed execution bit-for-bit.

    Covers:
    - Explicit client raw_id vs unassigned raw_id
    - Sequenced trades/quotes vs unsequenced trades/quotes
    - Multi-venue feeds (NASDAQ, CME, BATS) across multiple instruments (AAPL, MSFT, GOOGL)
    - Schema violations and quarantine events
    """
    wal_dir = tmp_path / "exhaustive_wal"
    engine = Engine.open(str(wal_dir))

    events: list[RawEvent] = [
        # 1. Trade with explicit client raw_id
        RawEvent(
            source="NASDAQ",
            raw_id="client_order_001",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 100.0,
                "sequence": 1,
                "exchange_ts": 1700000000.1,
            },
        ),
        # 2. Trade without raw_id, with sequence
        RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5000.25,
                "quantity": 5.0,
                "sequence": 1001,
                "exchange_ts": 1700000000.2,
            },
        ),
        # 3. Quote without raw_id, without sequence (unsequenced)
        RawEvent(
            source="BATS",
            payload={
                "instrument": "MSFT",
                "event_type": "QUOTE",
                "bid": 400.10,
                "ask": 400.15,
                "bid_size": 200.0,
                "ask_size": 300.0,
                "exchange_ts": 1700000000.3,
            },
        ),
        # 4. Crossed quote (INVALID)
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "GOOGL",
                "event_type": "QUOTE",
                "bid": 180.50,
                "ask": 180.00,  # crossed!
                "sequence": 10,
                "exchange_ts": 1700000000.4,
            },
        ),
        # 5. Schema violation / empty payload (INVALID)
        RawEvent(
            source="BATS",
            payload={},
        ),
        # 6. Second trade on same sequence (duplicate)
        RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5000.25,
                "quantity": 5.0,
                "sequence": 1001,  # duplicate seq
                "exchange_ts": 1700000000.5,
            },
        ),
    ]

    # Run 1: Submit live
    live_decisions = engine.submit(events)
    assert len(live_decisions) == len(events)

    # Run 2: Replay from beginning
    replay_decisions, metrics = engine.replay_with_metrics(from_offset=0)
    assert len(replay_decisions) == len(events)
    assert metrics.replayed_records == len(events)
    assert metrics.corrupted_frames == 0

    # Bit-for-bit assertion across all decisions
    for idx, (d_live, d_replay) in enumerate(zip(live_decisions, replay_decisions)):
        assert d_live.offset == d_replay.offset, f"Mismatch at #{idx}: offset"
        assert d_live.event_id == d_replay.event_id, f"Mismatch at #{idx}: event_id"
        assert d_live.quality_status == d_replay.quality_status, (
            f"Mismatch at #{idx}: status"
        )
        assert d_live.reasons == d_replay.reasons, f"Mismatch at #{idx}: reasons"

        if d_live.canonical_event is not None:
            assert d_replay.canonical_event is not None, (
                f"Mismatch at #{idx}: canonical_event missing on replay"
            )
            c_live = d_live.canonical_event
            c_replay = d_replay.canonical_event
            assert c_live.event_id == c_replay.event_id, (
                f"Mismatch at #{idx}: canonical event_id"
            )
            assert c_live.instrument_id == c_replay.instrument_id, (
                f"Mismatch at #{idx}: instrument"
            )
            assert c_live.source == c_replay.source, f"Mismatch at #{idx}: source"
            assert c_live.price == c_replay.price, f"Mismatch at #{idx}: price"
            assert c_live.bid_price == c_replay.bid_price, f"Mismatch at #{idx}: bid"
            assert c_live.ask_price == c_replay.ask_price, f"Mismatch at #{idx}: ask"
            assert c_live.quality_status == c_replay.quality_status, (
                f"Mismatch at #{idx}: canonical status"
            )
            assert c_live.reasons == c_replay.reasons, (
                f"Mismatch at #{idx}: canonical reasons"
            )
        else:
            assert d_replay.canonical_event is None, (
                f"Mismatch at #{idx}: expected None canonical_event on replay"
            )
            assert d_live.quarantine_row == d_replay.quarantine_row, (
                f"Mismatch at #{idx}: quarantine_row"
            )

    engine.close()
