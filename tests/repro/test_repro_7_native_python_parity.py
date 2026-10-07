"""Repro 7: Verify that Python and Native quality engines never disagree on evaluations,
specifically under per-instrument or custom lateness configurations.

Expected behavior:
Native and Python must evaluate identical event streams to identical QualityStatus and reasons.
If per-instrument overrides cannot be represented in native, native must be cleanly disabled
for that configuration rather than silently returning divergent results.

Current defect:
Python QualityEngine supports per-instrument QualityConfig (e.g. staleness_threshold_s = 0.5s),
while FastQualityEngine only holds single global scalar configuration in FastEngine.
For an event that is 200ms late:
Python -> VALID (0.2s <= 0.5s)
Native -> SUSPICIOUS/STALE (0.2s > default global 0.1s)
"""

from __future__ import annotations

import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.quality import QualityConfig, QualityEngine


@pytest.mark.xfail(
    strict=True,
    reason="Native fastpath and Python quality engine disagree on lateness evaluation under per-instrument configuration (Finding 7)",
)
def test_native_and_python_agree_on_per_instrument_lateness_configuration():
    from mdrap.fastpath import FastQualityEngine, is_available

    if not is_available():
        pytest.skip("Native fastpath not available in current environment")

    # Instrument config with generous staleness threshold (500ms)
    custom_cfg = QualityConfig(staleness_threshold_s=0.5)

    py_engine = QualityEngine(config=custom_cfg)
    native_engine = FastQualityEngine(staleness_threshold_s=0.1)  # Default global fastpath config

    # Event with 200ms network delay (receive_ts - exchange_ts = 0.200s)
    # Under custom_cfg (0.5s), this event is NOT stale.
    # Under default native config (0.1s), this event IS marked STALE.
    event_py = CanonicalEvent(
        event_id="ev_late_py",
        instrument_id="BTC-USDT",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.200,
        source="BINANCE",
        sequence_number=1,
        price=50000.0,
        quantity=1.0,
        quality_status=QualityStatus.VALID,
    )

    event_native = CanonicalEvent(
        event_id="ev_late_native",
        instrument_id="BTC-USDT",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.200,
        source="BINANCE",
        sequence_number=1,
        price=50000.0,
        quantity=1.0,
        quality_status=QualityStatus.VALID,
    )

    res_py = py_engine.evaluate(event_py)
    res_native = native_engine.evaluate(event_native)

    # Parity invariant: Python and Native MUST produce identical results
    assert res_py.quality_status == res_native.quality_status, (
        f"Engine divergence: Python status={res_py.quality_status.value} vs Native status={res_native.quality_status.value}"
    )
    assert set(res_py.reasons) == set(res_native.reasons), (
        f"Reason divergence: Python reasons={res_py.reasons} vs Native reasons={res_native.reasons}"
    )
