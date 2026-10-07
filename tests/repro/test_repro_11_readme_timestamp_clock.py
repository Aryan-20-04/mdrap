"""Repro 11: Verify that Pipeline and QualityEngine accept an injected Clock abstraction,
and that deterministic relative timestamps can be evaluated without relying on wall-clock time.

Expected behavior:
MDRAP provides a Clock Protocol with `SystemClock` (default) and `FixedClock(timestamp)`.
`Pipeline(clock=...)` and `QualityEngine(clock=...)` accept the injected clock.
Relative events constructed via `clock.now()` are evaluated deterministically regardless of real-world date.

Current defect:
No Clock protocol or FixedClock/SystemClock exists in mdrap.
Pipeline and QualityEngine rely directly on global `time.time()`, making timestamps in examples
and tests fragile or dependent on monkeypatching.
"""

from __future__ import annotations

import pytest


def test_clock_injection_in_pipeline_and_quality(tmp_path):
    # This must import Clock, FixedClock, SystemClock from mdrap.clock
    from mdrap.clock import FixedClock
    from mdrap.models import QualityStatus, RawEvent
    from mdrap.pipeline import Pipeline
    from mdrap.storage import Store

    base_time = 1700000000.0  # Deterministic reference timestamp
    clock = FixedClock(base_time)

    db_path = str(tmp_path / "events.db")
    store = Store(db_path)

    # Injected clock into Pipeline
    pipeline = Pipeline(store=store, clock=clock, journal=False, async_writer=False)

    # Generate an event using clock.now()
    raw = RawEvent(
        source="CLOCK_REPRO",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "exchange_ts": clock.now() - 0.05,  # 50ms ago relative to fixed clock
            "sequence": 1,
        },
        receive_timestamp=clock.now(),
    )

    result = pipeline.process_one(raw)
    assert result is not None
    assert result.quality_status == QualityStatus.VALID

    # Advance the fixed clock by 10 seconds to simulate elapsed time deterministically
    clock.advance(10.0)

    # Event with old timestamp relative to advanced clock should be marked STALE
    old_raw = RawEvent(
        source="CLOCK_REPRO",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": 150.2,
            "quantity": 10.0,
            "exchange_ts": base_time,  # 10s old relative to clock.now()
            "sequence": 2,
        },
        receive_timestamp=clock.now(),
    )

    old_result = pipeline.process_one(old_raw)
    assert old_result is not None
    assert old_result.quality_status == QualityStatus.SUSPICIOUS
    assert "STALE" in old_result.reasons
