"""Tests for HALT and STATUS market data events and watchdog staleness suppression (Item 2.2)."""

import time
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.gateway import Gateway, SchemaError
from mdrap.quality import QualityEngine, QualityConfig
from mdrap.engine import Engine, EngineConfig
from mdrap.watchdog import SourceWatchdog


def test_gateway_normalize_halt_and_status_events():
    gw = Gateway(run_id="test-halt")

    # 1. Normal HALT event (no price or bid/ask required)
    raw_halt = RawEvent(
        source="BINANCE",
        receive_timestamp=1000.0,
        payload={
            "instrument": "BTC-USDT",
            "event_type": "HALT",
            "exchange_ts": 1000.0,
            "status": "HALTED",
            "reason": "CIRCUIT_BREAKER",
        },
    )
    canon_halt = gw.normalize(raw_halt)
    assert canon_halt.instrument_id == "BTC-USDT"
    assert canon_halt.event_type == EventType.HALT
    assert canon_halt.price is None
    assert canon_halt.bid_price is None

    # 2. Normal STATUS event
    raw_status = RawEvent(
        source="BINANCE",
        receive_timestamp=1050.0,
        payload={
            "instrument": "BTC-USDT",
            "event_type": "STATUS",
            "exchange_ts": 1050.0,
            "status": "RESUME",
        },
    )
    canon_status = gw.normalize(raw_status)
    assert canon_status.instrument_id == "BTC-USDT"
    assert canon_status.event_type == EventType.STATUS

    # 3. Missing instrument must fail
    with pytest.raises(SchemaError):
        gw.normalize(
            RawEvent(
                source="BINANCE",
                receive_timestamp=1000.0,
                payload={"event_type": "HALT"},
            )
        )


def test_quality_engine_accepts_halt_and_status():
    qe = QualityEngine()

    ev_halt = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.HALT,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.0,
        processing_timestamp=1000.0,
        source="NASDAQ",
        sequence_number=1,
    )
    res_halt = qe.evaluate(ev_halt)
    assert res_halt is not None
    assert res_halt.quality_status == QualityStatus.VALID
    assert len(res_halt.reasons) == 0

    # Even with large receive-exchange timestamp delta, HALT is not flagged as STALE
    ev_halt_delayed = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.HALT,
        exchange_timestamp=1000.0,
        receive_timestamp=2000.0,  # 1000s later
        processing_timestamp=2000.0,
        source="NASDAQ",
        sequence_number=2,
    )
    res_delayed = qe.evaluate(ev_halt_delayed)
    assert res_delayed is not None
    assert "STALE" not in res_delayed.reasons
    assert res_delayed.quality_status == QualityStatus.VALID


def test_engine_processes_halt_and_status():
    engine = Engine()

    raw_halt = RawEvent(
        source="SIP",
        receive_timestamp=100.0,
        payload={
            "instrument": "TSLA",
            "event_type": "HALT",
            "exchange_ts": 100.0,
            "status": "HALTED",
        },
    )
    state, dec = engine.step(engine.state, raw_halt, engine.clock, offset=0)
    assert dec.quality_status == QualityStatus.VALID
    assert dec.canonical_event is not None
    assert dec.canonical_event.event_type == EventType.HALT
    assert dec.canonical_event.instrument_id == "TSLA"

    raw_status = RawEvent(
        source="SIP",
        receive_timestamp=200.0,
        payload={
            "instrument": "TSLA",
            "event_type": "STATUS",
            "exchange_ts": 200.0,
            "status": "RESUMED",
        },
    )
    state, dec2 = engine.step(state, raw_status, engine.clock, offset=1)
    assert dec2.quality_status == QualityStatus.VALID
    assert dec2.canonical_event is not None
    assert dec2.canonical_event.event_type == EventType.STATUS


def test_watchdog_halt_staleness_suppression():
    watchdog = SourceWatchdog(silence_threshold_s=2.0)

    # Initial state: not halted
    assert not watchdog.is_halted("MSFT")
    # A gap of 10s should be marked stale
    assert (
        watchdog.check_instrument_staleness(
            "MSFT", last_ts=100.0, current_time=110.0, threshold_s=2.0
        )
        is True
    )

    # Observe HALT event for MSFT
    halt_ev = CanonicalEvent(
        event_id="h1",
        instrument_id="MSFT",
        event_type=EventType.HALT,
        exchange_timestamp=100.0,
        receive_timestamp=100.0,
        processing_timestamp=100.0,
        source="FEED_A",
    )
    watchdog.observe(halt_ev)
    assert watchdog.is_halted("MSFT")
    assert "MSFT" in watchdog.halted_instruments()

    # Invariant 2.2: An instrument in halt is legitimately silent and must NOT be flagged as STALE
    assert (
        watchdog.check_instrument_staleness(
            "MSFT", last_ts=100.0, current_time=500.0, threshold_s=2.0
        )
        is False
    )

    # Observe STATUS event indicating resume
    class StatusWrapper:
        source = "FEED_A"
        instrument_id = "MSFT"
        event_type = EventType.STATUS
        exchange_timestamp = 505.0
        receive_timestamp = 505.0
        payload = {"status": "RESUME"}

    watchdog.observe(StatusWrapper())
    assert not watchdog.is_halted("MSFT")

    # Now that it has resumed, an unhalted instrument with excessive silence IS stale
    assert (
        watchdog.check_instrument_staleness(
            "MSFT", last_ts=505.0, current_time=520.0, threshold_s=2.0
        )
        is True
    )
