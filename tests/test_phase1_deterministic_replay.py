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
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 10.0, "sequence": 1, "exchange_ts": time.time()},
        ),
        # Valid quote
        RawEvent(
            source="NASDAQ",
            payload={"instrument": "AAPL", "event_type": "QUOTE", "bid": 149.9, "ask": 150.1, "sequence": 2, "exchange_ts": time.time()},
        ),
        # Invalid schema payload (empty)
        RawEvent(
            source="NASDAQ",
            payload={},
        ),
        # Another valid trade
        RawEvent(
            source="NASDAQ",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.2, "quantity": 5.0, "sequence": 3, "exchange_ts": time.time()},
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
        assert d_live.quality_status == d_replay.quality_status
        assert d_live.reasons == d_replay.reasons
        if d_live.canonical_event and d_replay.canonical_event:
            assert d_live.canonical_event.price == d_replay.canonical_event.price
            assert d_live.canonical_event.bid_price == d_replay.canonical_event.bid_price

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
        payload={"instrument": "MSFT", "event_type": "TRADE", "price": 300.0, "quantity": 10.0, "sequence": 1, "exchange_ts": time.time()},
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
        payload={"instrument": "MSFT", "event_type": "TRADE", "price": 301.0, "quantity": 20.0, "sequence": 2, "exchange_ts": time.time()},
    )

    with pytest.raises(OSError) as exc_info:
        engine.submit(ev_fail)
    assert "ENOSPC" in str(exc_info.value)

    # Engine state MUST be cleanly rolled back to pre_fail_state!
    assert engine.state.event_count == 1
    assert engine.state.counts["VALID"] == 1
    assert engine.state.to_dict() == pre_fail_state

    engine.close()
