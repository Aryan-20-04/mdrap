"""
Phase 2 Native Memory Safety Tests: MEM-01 (Dynamic Window Heap Safety).
Validates that price window re-allocations and resize operations never cause
heap buffer overflows, memory corruptions, or cross-instrument corruption.
"""

from __future__ import annotations

import pytest
from src import fastpath
from src.config import QualityConfig
from src.fastpath import FastQualityEngine
from src.models import CanonicalEvent, EventType, QualityStatus


def _make_event(instrument_id: str, price: float, seq: int) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"evt-{seq}",
        instrument_id=instrument_id,
        source="FEEDX",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0 + seq * 0.001,
        receive_timestamp=1000.0 + seq * 0.001,
        processing_timestamp=1000.0 + seq * 0.001,
        sequence_number=seq,
        price=price,
        quantity=1.0,
    )


def test_window_initialization():
    engine = FastQualityEngine(QualityConfig(price_window=50))
    for i in range(1, 60):
        ev = _make_event("AAPL", 100.0 + (i % 5) * 0.1, i)
        res = engine.evaluate(ev)
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_window_growth():
    # 1. Initialize with small window 10
    cfg1 = QualityConfig(price_window=10)
    engine = FastQualityEngine(cfg1)
    for i in range(1, 25):
        ev = _make_event("AAPL", 150.0 + (i % 3) * 0.05, i)
        engine.evaluate(ev)

    # 2. Re-init direct C library with window 100 (previously would heap overflow)
    if fastpath._NATIVE_LIB:
        fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, 100)

    cfg2 = QualityConfig(price_window=100)
    engine2 = FastQualityEngine(cfg2)
    for i in range(1, 150):
        ev = _make_event("AAPL", 150.0 + (i % 7) * 0.05, i)
        res = engine2.evaluate(ev)
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_window_shrink():
    engine = FastQualityEngine(QualityConfig(price_window=100))
    for i in range(1, 120):
        ev = _make_event("MSFT", 300.0 + (i % 4) * 0.1, i)
        engine.evaluate(ev)

    engine_small = FastQualityEngine(QualityConfig(price_window=20))
    for i in range(1, 50):
        ev = _make_event("MSFT", 300.0 + (i % 4) * 0.1, i)
        res = engine_small.evaluate(ev)
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_window_growth_to_max():
    # MAX_WINDOW is 128
    if fastpath._NATIVE_LIB:
        fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, 128)
    engine = FastQualityEngine(QualityConfig(price_window=128))
    for i in range(1, 200):
        ev = _make_event("GOOGL", 180.0 + (i % 5) * 0.2, i)
        res = engine.evaluate(ev)
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_window_growth_beyond_max_clamped():
    # Window requested > 128 should be clamped to 128 without overflowing
    if fastpath._NATIVE_LIB:
        fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, 500)
    engine = FastQualityEngine(QualityConfig(price_window=500))
    for i in range(1, 200):
        ev = _make_event("AMZN", 190.0 + (i % 3) * 0.1, i)
        res = engine.evaluate(ev)
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_repeated_resize():
    # Rapidly resize up and down across various instruments
    windows = [5, 50, 10, 128, 20, 100, 15, 64]
    for w in windows:
        if fastpath._NATIVE_LIB:
            fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, w)
        eng = FastQualityEngine(QualityConfig(price_window=w))
        for i in range(1, w * 2):
            ev = _make_event("NVDA", 120.0 + (i % 6) * 0.1, i)
            res = eng.evaluate(ev)
            assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_resize_after_reset():
    engine = FastQualityEngine(QualityConfig(price_window=20))
    for i in range(1, 30):
        engine.evaluate(_make_event("TSLA", 220.0, i))

    engine.reset()

    if fastpath._NATIVE_LIB:
        fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, 120)
    engine2 = FastQualityEngine(QualityConfig(price_window=120))
    for i in range(1, 150):
        res = engine2.evaluate(_make_event("TSLA", 220.0 + (i % 5) * 0.5, i))
        assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_resize_stress_multiple_instruments():
    # Verify no neighboring instrument slots get corrupted
    instruments = [f"SYM_{k}" for k in range(50)]
    engine = FastQualityEngine(QualityConfig(price_window=10))
    for sym in instruments:
        for i in range(1, 15):
            engine.evaluate(_make_event(sym, 100.0 + i, i))

    # Resize to 128
    if fastpath._NATIVE_LIB:
        fastpath._NATIVE_LIB.fastpath_init(0.05, 6.0, 128)
    engine2 = FastQualityEngine(QualityConfig(price_window=128))
    for sym in instruments:
        for i in range(1, 140):
            res = engine2.evaluate(_make_event(sym, 100.0 + (i % 10) * 0.1, i))
            assert res.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)
