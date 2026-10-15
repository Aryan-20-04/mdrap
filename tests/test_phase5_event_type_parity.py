"""Phase 5 Event-Type Validation & Native/Python Parity Test Suite.

Verifies:
- F-09: Missing, unknown, or UNKNOWN event types are strictly quarantined as INVALID with SCHEMA_VIOLATION.
- F-09: Structural validation: TRADE requires price; QUOTE requires at least one of bid or ask.
- F-10: Native C hotpath kernel and pure Python QualityEngine produce bit-identical decisions across all event types.
- F-12: Full parity verification between compiled C acceleration and pure-Python fallback.
"""

from __future__ import annotations

import os
import pytest

from mdrap.engine import Engine
from mdrap.fastpath import FastQualityEngine, is_available
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from mdrap.quality import QualityEngine


# ---------------------------------------------------------------------------
# F-09: Engine Event Type & Structural Schema Validation
# ---------------------------------------------------------------------------


def test_f09_engine_quarantines_missing_event_type() -> None:
    """F-09: Engine must reject events where event_type is omitted."""
    with Engine.open(None) as engine:
        raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.0,
            payload={"instrument": "AAPL", "price": 150.0, "quantity": 10.0},
        )
        dec = engine.submit(raw)
        assert dec.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in dec.reasons
        assert dec.canonical_event is None
        assert dec.quarantine_row is not None
        assert dec.quarantine_row[3] == QualityStatus.INVALID.value


def test_f09_engine_quarantines_unknown_string_event_type() -> None:
    """F-09: Engine must reject unknown string event types as INVALID."""
    with Engine.open(None) as engine:
        raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.0,
            payload={
                "instrument": "AAPL",
                "price": 150.0,
                "event_type": "UNKNOWN_GARBAGE",
            },
        )
        dec = engine.submit(raw)
        assert dec.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in dec.reasons
        assert dec.canonical_event is None
        assert dec.quarantine_row is not None


def test_f09_engine_quarantines_unknown_enum_event_type() -> None:
    """F-09: Engine must reject EventType.UNKNOWN as INVALID."""
    with Engine.open(None) as engine:
        for ev_type in (EventType.UNKNOWN, "UNKNOWN", "unknown"):
            raw = RawEvent(
                source="FEED_A",
                receive_timestamp=1000.0,
                payload={
                    "instrument": "AAPL",
                    "price": 150.0,
                    "event_type": ev_type,
                },
            )
            dec = engine.submit(raw)
            assert dec.quality_status == QualityStatus.INVALID
            assert Reason.SCHEMA_VIOLATION.value in dec.reasons
            assert dec.canonical_event is None
            assert dec.quarantine_row is not None


def test_f09_engine_quarantines_trade_without_price() -> None:
    """F-09: TRADE event without price must be quarantined as INVALID."""
    with Engine.open(None) as engine:
        raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.0,
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "quantity": 100.0,
            },
        )
        dec = engine.submit(raw)
        assert dec.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in dec.reasons
        assert dec.canonical_event is None
        assert dec.quarantine_row is not None


def test_f09_engine_quarantines_quote_without_bid_or_ask() -> None:
    """F-09: QUOTE event without bid and ask must be quarantined as INVALID."""
    with Engine.open(None) as engine:
        raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.0,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid_size": 10.0,
            },
        )
        dec = engine.submit(raw)
        assert dec.quality_status == QualityStatus.INVALID
        assert Reason.SCHEMA_VIOLATION.value in dec.reasons
        assert dec.canonical_event is None
        assert dec.quarantine_row is not None


def test_f09_engine_accepts_valid_trade_and_quote() -> None:
    """Engine must accept properly formed TRADE and QUOTE events as VALID."""
    with Engine.open(None) as engine:
        trade_raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.0,
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10.0,
                "sequence": 1,
                "exchange_ts": 1000.0,
            },
        )
        dec_t = engine.submit(trade_raw)
        assert dec_t.quality_status == QualityStatus.VALID
        assert dec_t.canonical_event is not None
        assert dec_t.canonical_event.event_type == EventType.TRADE

        quote_raw = RawEvent(
            source="FEED_A",
            receive_timestamp=1000.001,
            payload={
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 149.9,
                "ask": 150.1,
                "sequence": 2,
                "exchange_ts": 1000.001,
            },
        )
        dec_q = engine.submit(quote_raw)
        assert dec_q.quality_status == QualityStatus.VALID
        assert dec_q.canonical_event is not None
        assert dec_q.canonical_event.event_type == EventType.QUOTE


# ---------------------------------------------------------------------------
# F-10 & F-12: Native C Fastpath vs Python QualityEngine Parity
# ---------------------------------------------------------------------------


def _make_canonical(
    event_type: Any,
    price: float | None = 100.0,
    bid: float | None = None,
    ask: float | None = None,
    seq: int = 1,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"ev_{seq}",
        instrument_id="AAPL",
        event_type=event_type,
        exchange_timestamp=1000.0 + seq,
        receive_timestamp=1000.001 + seq,
        processing_timestamp=0.0,
        source="FEEDX",
        sequence_number=seq,
        price=price,
        quantity=10.0,
        bid_price=bid,
        ask_price=ask,
    )


@pytest.mark.parametrize(
    "ev_type, price, bid, ask, expected_status",
    [
        (EventType.TRADE, 100.0, None, None, QualityStatus.VALID),
        (EventType.QUOTE, None, 99.5, 100.5, QualityStatus.VALID),
        (EventType.TRADE, None, None, None, QualityStatus.INVALID),  # missing price
        (EventType.QUOTE, None, None, None, QualityStatus.INVALID),  # missing bid/ask
        (EventType.QUOTE, None, 101.0, 100.0, QualityStatus.INVALID),  # crossed quote
        (EventType.UNKNOWN, 100.0, None, None, QualityStatus.INVALID),
        (EventType.BOOK, 100.0, None, None, QualityStatus.INVALID),
        (EventType.DEPTH, 100.0, None, None, QualityStatus.INVALID),
        ("UNKNOWN", 100.0, None, None, QualityStatus.INVALID),
        ("GARBAGE", 100.0, None, None, QualityStatus.INVALID),
    ],
)
def test_f10_c_fastpath_vs_python_parity(
    ev_type: Any,
    price: float | None,
    bid: float | None,
    ask: float | None,
    expected_status: QualityStatus,
) -> None:
    """F-10: FastQualityEngine and QualityEngine must produce identical status and reasons."""
    ev_c = _make_canonical(ev_type, price=price, bid=bid, ask=ask, seq=100)
    ev_py = _make_canonical(ev_type, price=price, bid=bid, ask=ask, seq=100)

    c_eng = FastQualityEngine()
    py_eng = QualityEngine()

    res_c = c_eng.evaluate(ev_c)
    res_py = py_eng.evaluate(ev_py)

    assert res_c.quality_status == expected_status, f"C returned {res_c.quality_status}, expected {expected_status}"
    assert res_py.quality_status == expected_status, f"Py returned {res_py.quality_status}, expected {expected_status}"
    assert res_c.quality_status == res_py.quality_status
    assert set(res_c.reasons) == set(res_py.reasons)


def test_f10_fastpath_batch_evaluate_parity() -> None:
    """F-10: FastQualityEngine.evaluate_batch must match individual evaluate results."""
    test_specs = [
        (EventType.TRADE, 100.0, None, None, 1),
        (EventType.QUOTE, None, 99.5, 100.5, 2),
        (EventType.TRADE, None, None, None, 3),  # Invalid: no price
        (EventType.QUOTE, None, 101.0, 100.0, 4),  # Invalid: crossed
        (EventType.UNKNOWN, 100.0, None, None, 5),  # Invalid: unknown type
        (EventType.DEPTH, 100.0, None, None, 6),  # Invalid: depth
    ]

    events_single = [_make_canonical(t, px, b, a, seq) for t, px, b, a, seq in test_specs]
    events_batch = [_make_canonical(t, px, b, a, seq) for t, px, b, a, seq in test_specs]

    eng_single = FastQualityEngine()
    eng_batch = FastQualityEngine()

    results_single = [eng_single.evaluate(e) for e in events_single]
    results_batch = eng_batch.evaluate_batch(events_batch)

    assert len(results_single) == len(results_batch)
    for idx, (res_s, res_b) in enumerate(zip(results_single, results_batch)):
        assert (
            res_s.quality_status == res_b.quality_status
        ), f"Batch mismatch at {idx}: single={res_s.quality_status}, batch={res_b.quality_status}"
        assert set(res_s.reasons) == set(
            res_b.reasons
        ), f"Reason mismatch at {idx}: single={res_s.reasons}, batch={res_b.reasons}"


def test_f12_fastpath_fallback_parity_when_native_disabled(monkeypatch) -> None:
    """F-12: Verify FastQualityEngine under fallback mode matches QualityEngine directly."""
    monkeypatch.setenv("MDRAP_DISABLE_FASTPATH", "1")
    eng_fallback = FastQualityEngine()
    py_eng = QualityEngine()

    for i, ev_type in enumerate((EventType.TRADE, EventType.QUOTE, EventType.UNKNOWN, "GARBAGE")):
        ev1 = _make_canonical(ev_type, price=100.0, bid=99.0, ask=101.0, seq=50 + i)
        ev2 = _make_canonical(ev_type, price=100.0, bid=99.0, ask=101.0, seq=50 + i)

        r1 = eng_fallback.evaluate(ev1)
        r2 = py_eng.evaluate(ev2)

        assert r1.quality_status == r2.quality_status
        assert set(r1.reasons) == set(r2.reasons)
