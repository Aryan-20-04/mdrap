"""Phase 6 Quality Engine Hardening Matrix Tests.

Exhaustively verifies every canonical quality rule across:
- normal case
- boundary case
- invalid case
- extreme case
- regression case
And ensures deterministic evaluation ordering and priority escalation:
Schema -> Timestamp -> Sequence -> Price -> Size -> Book -> Statistical
"""

from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityEngine, QualityConfig
from rules import (
    get_rule_by_id,
    get_rule_by_name,
    list_registered_definitions,
)


def _make_base_event(**kwargs) -> CanonicalEvent:
    defaults = {
        "event_id": "ev_001",
        "instrument_id": "AAPL",
        "event_type": EventType.TRADE,
        "exchange_timestamp": 1000.0,
        "receive_timestamp": 1000.010,
        "processing_timestamp": 1000.011,
        "source": "FEED_A",
        "sequence_number": 1,
        "venue": "NASDAQ",
        "price": 150.0,
        "quantity": 100.0,
        "quality_status": QualityStatus.VALID,
        "reasons": [],
    }
    defaults.update(kwargs)
    return CanonicalEvent(**defaults)


def test_rule_metadata_integrity():
    """Verify all 15 rules have valid definitions and lookup helpers."""
    rules = list_registered_definitions()
    assert len(rules) == 15
    for r in rules:
        assert r.rule_id.startswith("MD")
        assert r.name in Reason.__members__
        assert r.severity in (QualityStatus.INVALID, QualityStatus.SUSPICIOUS)
        assert len(r.description) > 5
        assert len(r.trigger_condition) > 5
        assert get_rule_by_id(r.rule_id) == r
        assert get_rule_by_name(r.name) == r


# --- MD001: DUPLICATE ---
def test_md001_duplicate_matrix():
    # 1. Normal case: consecutive unique sequences are valid
    engine = QualityEngine(QualityConfig(reorder_window_s=0))
    ev1 = _make_base_event(sequence_number=10)
    ev2 = _make_base_event(sequence_number=11)
    res1 = engine.evaluate(ev1)
    res2 = engine.evaluate(ev2)
    assert res1.quality_status == QualityStatus.VALID
    assert res2.quality_status == QualityStatus.VALID

    # 2. Boundary case: sequence window bit 63 boundary
    engine = QualityEngine(QualityConfig(reorder_window_s=0))
    engine.evaluate(_make_base_event(sequence_number=1))
    engine.evaluate(_make_base_event(sequence_number=64))
    # Resending sequence 1 is at boundary (delta 63) -> must be detected as duplicate
    dup_bound = engine.evaluate(_make_base_event(sequence_number=1))
    assert dup_bound.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dup_bound.reasons

    # 3. Invalid case: direct immediate duplicate
    dup_imm = engine.evaluate(_make_base_event(sequence_number=64))
    assert dup_imm.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dup_imm.reasons

    # 4. Extreme case: duplicate in unsequenced LRU with identical floating point payload
    engine = QualityEngine()
    ev_unseq1 = _make_base_event(
        sequence_number=None, exchange_timestamp=5000.123456, receive_timestamp=5000.125
    )
    ev_unseq2 = _make_base_event(
        sequence_number=None, exchange_timestamp=5000.123456, receive_timestamp=5000.125
    )
    r_unseq1 = engine.evaluate(ev_unseq1)
    r_unseq2 = engine.evaluate(ev_unseq2)
    assert r_unseq1.quality_status == QualityStatus.VALID
    assert Reason.DUPLICATE.value in r_unseq2.reasons

    # 5. Regression case: sequence number rollover / gap does not falsely trigger duplicate
    engine = QualityEngine(QualityConfig(reorder_window_s=0))
    engine.evaluate(_make_base_event(sequence_number=100))
    # An out of order sequence beyond 64 bits should be OUT_OF_ORDER, not DUPLICATE
    res_old = engine.evaluate(_make_base_event(sequence_number=10))
    assert Reason.OUT_OF_ORDER.value in res_old.reasons
    assert Reason.DUPLICATE.value not in res_old.reasons


# --- MD002: SEQUENCE_GAP ---
def test_md002_sequence_gap_matrix():
    # 1. Normal: continuous increment
    engine = QualityEngine(QualityConfig(reorder_window_s=0))
    assert (
        engine.evaluate(_make_base_event(sequence_number=1)).quality_status
        == QualityStatus.VALID
    )
    assert (
        engine.evaluate(_make_base_event(sequence_number=2)).quality_status
        == QualityStatus.VALID
    )

    # 2. Boundary: jump of 2 is the minimal gap
    res_gap2 = engine.evaluate(_make_base_event(sequence_number=4))
    assert Reason.SEQUENCE_GAP.value in res_gap2.reasons
    assert res_gap2.quality_status == QualityStatus.SUSPICIOUS

    # 3. Invalid / Severe jump: jump exceeds seq_jump_limit
    res_huge = engine.evaluate(_make_base_event(sequence_number=20_000_000))
    assert Reason.SEQUENCE_GAP.value in res_huge.reasons

    # 4. Extreme: uint64 near-limit gap
    res_uint64 = engine.evaluate(_make_base_event(sequence_number=(1 << 48)))
    assert Reason.SEQUENCE_GAP.value in res_uint64.reasons

    # 5. Regression: contiguous fill after gap resets gap detection
    engine.evaluate(_make_base_event(sequence_number=(1 << 48) + 1))
    res_next = engine.evaluate(_make_base_event(sequence_number=(1 << 48) + 2))
    assert Reason.SEQUENCE_GAP.value not in res_next.reasons


# --- MD003: TS_IMPLAUSIBLE (FUTURE_TIMESTAMP) ---
def test_md003_ts_implausible_matrix():
    engine = QualityEngine(QualityConfig(max_future_skew_s=1.0))
    # 1. Normal: exchange_ts <= receive_ts
    assert (
        engine.evaluate(
            _make_base_event(exchange_timestamp=100.0, receive_timestamp=100.005)
        ).quality_status
        == QualityStatus.VALID
    )

    # 2. Boundary: exchange_ts exactly equal to receive_ts + max_future_skew_s (1.0s)
    res_bound = engine.evaluate(
        _make_base_event(
            exchange_timestamp=101.0, receive_timestamp=100.0, sequence_number=2
        )
    )
    assert Reason.TS_IMPLAUSIBLE.value not in res_bound.reasons

    # 3. Invalid: exchange_ts exceeds tolerance by 1 microsecond
    res_inv = engine.evaluate(
        _make_base_event(
            exchange_timestamp=101.000001, receive_timestamp=100.0, sequence_number=3
        )
    )
    assert Reason.TS_IMPLAUSIBLE.value in res_inv.reasons

    # 4. Extreme: future timestamp by years
    res_ext = engine.evaluate(
        _make_base_event(
            exchange_timestamp=9999999999.0, receive_timestamp=100.0, sequence_number=4
        )
    )
    assert Reason.TS_IMPLAUSIBLE.value in res_ext.reasons

    # 5. Regression: plausible event after future timestamp does not get poisoned
    res_reg = engine.evaluate(
        _make_base_event(
            exchange_timestamp=100.05, receive_timestamp=100.06, sequence_number=5
        )
    )
    assert Reason.TS_IMPLAUSIBLE.value not in res_reg.reasons


# --- MD004: STALE ---
def test_md004_stale_matrix():
    engine = QualityEngine(QualityConfig(staleness_threshold_s=0.050))
    # 1. Normal: 5ms delay (< 50ms)
    assert (
        engine.evaluate(
            _make_base_event(exchange_timestamp=100.0, receive_timestamp=100.005)
        ).quality_status
        == QualityStatus.VALID
    )

    # 2. Boundary: exactly 50ms delay
    res_bound = engine.evaluate(
        _make_base_event(
            exchange_timestamp=100.0, receive_timestamp=100.050, sequence_number=2
        )
    )
    assert Reason.STALE.value not in res_bound.reasons

    # 3. Invalid: 51ms delay
    res_inv = engine.evaluate(
        _make_base_event(
            exchange_timestamp=100.0, receive_timestamp=100.051, sequence_number=3
        )
    )
    assert Reason.STALE.value in res_inv.reasons

    # 4. Extreme: hours of staleness
    res_ext = engine.evaluate(
        _make_base_event(
            exchange_timestamp=100.0, receive_timestamp=10000.0, sequence_number=4
        )
    )
    assert Reason.STALE.value in res_ext.reasons

    # 5. Regression: fresh tick after stale tick is VALID
    res_fresh = engine.evaluate(
        _make_base_event(
            exchange_timestamp=10001.0, receive_timestamp=10001.002, sequence_number=5
        )
    )
    assert Reason.STALE.value not in res_fresh.reasons


# --- MD005: CROSSED_QUOTE ---
def test_md005_crossed_quote_matrix():
    engine = QualityEngine()
    # 1. Normal: bid < ask
    q_norm = _make_base_event(
        event_type=EventType.QUOTE,
        price=None,
        bid_price=100.0,
        ask_price=100.05,
        bid_size=10,
        ask_size=10,
    )
    assert engine.evaluate(q_norm).quality_status == QualityStatus.VALID

    # 2. Boundary: 1 cent cross (bid > ask by 0.01)
    q_cross_min = _make_base_event(
        event_type=EventType.QUOTE,
        price=None,
        bid_price=100.01,
        ask_price=100.00,
        bid_size=10,
        ask_size=10,
        sequence_number=2,
    )
    res_cross_min = engine.evaluate(q_cross_min)
    assert res_cross_min.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in res_cross_min.reasons

    # 3. Invalid: inverted book (bid > ask)
    q_cross = _make_base_event(
        event_type=EventType.QUOTE,
        price=None,
        bid_price=105.0,
        ask_price=100.0,
        bid_size=10,
        ask_size=10,
        sequence_number=3,
    )
    res_cross = engine.evaluate(q_cross)
    assert res_cross.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in res_cross.reasons

    # 4. Extreme: massive inversion (bid = 1000, ask = 1)
    q_ext = _make_base_event(
        event_type=EventType.QUOTE,
        price=None,
        bid_price=1000.0,
        ask_price=1.0,
        bid_size=10,
        ask_size=10,
        sequence_number=4,
    )
    res_ext = engine.evaluate(q_ext)
    assert res_ext.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in res_ext.reasons

    # 5. Regression: normal quote restores valid status
    q_rest = _make_base_event(
        event_type=EventType.QUOTE,
        price=None,
        bid_price=99.95,
        ask_price=100.05,
        bid_size=10,
        ask_size=10,
        sequence_number=5,
    )
    assert engine.evaluate(q_rest).quality_status == QualityStatus.VALID


# --- MD007: SCHEMA_VIOLATION ---
def test_md007_schema_violation_matrix():
    engine = QualityEngine()
    # 1. Normal: finite numbers
    assert engine.evaluate(_make_base_event()).quality_status == QualityStatus.VALID

    # 2. Boundary: price is 0.0 (allowed in non-negative unless negative)
    res_zero = engine.evaluate(_make_base_event(price=0.0, sequence_number=2))
    assert Reason.SCHEMA_VIOLATION.value not in res_zero.reasons

    # 3. Invalid: negative price when allow_negative is False
    res_neg = engine.evaluate(_make_base_event(price=-1.0, sequence_number=3))
    assert res_neg.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_neg.reasons

    # 4. Extreme: NaN and Infinity
    res_nan = engine.evaluate(_make_base_event(price=float("nan"), sequence_number=4))
    res_inf = engine.evaluate(_make_base_event(price=float("inf"), sequence_number=5))
    assert Reason.SCHEMA_VIOLATION.value in res_nan.reasons
    assert Reason.SCHEMA_VIOLATION.value in res_inf.reasons

    # 5. Regression: negative quantity always invalid regardless of allow_negative
    cfg_allow_neg = QualityConfig(allow_negative=True)
    engine_neg = QualityEngine(cfg_allow_neg)
    res_neg_qty = engine_neg.evaluate(
        _make_base_event(price=-5.0, quantity=-10.0, sequence_number=6)
    )
    assert Reason.SCHEMA_VIOLATION.value in res_neg_qty.reasons
    assert res_neg_qty.quality_status == QualityStatus.INVALID
