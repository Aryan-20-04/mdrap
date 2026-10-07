"""Tests for pure deterministic Engine fold: state transitions, replay, snapshot/restore."""

import copy
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine, EngineState
from mdrap.models import QualityStatus, RawEvent


def test_engine_deterministic_step_and_replay():
    engine = Engine()
    clock = FixedClock(1700000000.0)

    events = [
        RawEvent(
            source="NASDAQ",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i * 0.001},
            receive_timestamp=1700000000.0 + i * 0.001,
        )
        for i in range(10)
    ]

    # Run 1
    state1 = engine.create_initial_state()
    decisions1 = []
    for ev in events:
        state1, dec = engine.step(state1, ev, clock)
        decisions1.append(dec)

    # Run 2: Replay same events from fresh state
    state2 = engine.create_initial_state()
    decisions2 = []
    for ev in events:
        state2, dec = engine.step(state2, ev, clock)
        decisions2.append(dec)

    # Verify identical decisions, identical event IDs, and identical canonical rows
    assert len(decisions1) == len(decisions2) == 10
    for d1, d2 in zip(decisions1, decisions2):
        assert d1.event_id == d2.event_id
        assert d1.quality_status == d2.quality_status
        assert d1.canonical_event.event_id == d2.canonical_event.event_id
        assert d1.canonical_event.price == d2.canonical_event.price
        assert d1.canonical_event.exchange_timestamp == d2.canonical_event.exchange_timestamp


def test_engine_snapshot_and_restore():
    engine = Engine()
    clock = FixedClock(1700000000.0)

    events = [
        RawEvent(
            source="CME",
            payload={"instrument": "ES", "event_type": "TRADE", "price": 4500.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i * 0.001},
            receive_timestamp=1700000000.0 + i * 0.001,
        )
        for i in range(5)
    ]

    state = engine.create_initial_state()
    for ev in events:
        state, _ = engine.step(state, ev, clock)

    # Snapshot state
    snap_bytes = engine.snapshot(state)
    assert isinstance(snap_bytes, bytes)

    # Restore in a new state
    restored_state = engine.restore(snap_bytes)
    assert restored_state.event_count == state.event_count
    assert restored_state.sequence_state == state.sequence_state
    assert restored_state.counts == state.counts

    # Step next event from both states; verify identical outcome
    next_ev = RawEvent(
        source="CME",
        payload={"instrument": "ES", "event_type": "TRADE", "price": 4510.0, "sequence": 6, "exchange_ts": 1700000000.006},
        receive_timestamp=1700000000.006,
    )

    state, dec_orig = engine.step(state, next_ev, clock)
    restored_state, dec_restored = engine.step(restored_state, next_ev, clock)

    assert dec_orig.event_id == dec_restored.event_id
    assert dec_orig.quality_status == dec_restored.quality_status
    assert dec_orig.canonical_event.price == dec_restored.canonical_event.price


def test_engine_poison_and_invalid_payloads_quarantined():
    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    bad_events = [
        RawEvent(source="BAD", payload=None),
        RawEvent(source="BAD", payload="not a dict"),
        RawEvent(source="BAD", payload={"instrument": "AAPL", "sequence": 2**70}),  # integer overflow
        RawEvent(source="BAD", payload={"instrument": "AAPL", "price": float("nan")}),  # NaN price
    ]

    for ev in bad_events:
        state, dec = engine.step(state, ev, clock)
        assert dec.quality_status == QualityStatus.INVALID
        assert dec.quarantine_row is not None
        assert dec.canonical_event is None

    # Healthy event following poison events processes cleanly
    healthy_ev = RawEvent(
        source="HEALTHY",
        payload={"instrument": "AAPL", "price": 150.0, "sequence": 1, "exchange_ts": 1700000000.0},
        receive_timestamp=1700000000.0,
    )
    state, dec_healthy = engine.step(state, healthy_ev, clock)
    assert dec_healthy.quality_status == QualityStatus.VALID
    assert dec_healthy.canonical_event is not None
