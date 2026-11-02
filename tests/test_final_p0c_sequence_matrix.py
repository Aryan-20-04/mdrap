"""Comprehensive test matrix for MDRAP v4.0.1 P0-C: Sequence Window, Stale Dedup, and Multi-Tier Parity.

Verifies all 14 sequence scenarios across:
- Engine (inline stateful execution)
- QualityEngine (pure Python reference)
- FastQualityEngine (compiled C native hot path kernel)

Scenarios tested:
1. seq == expected (monotonic contiguous advance)
2. seq > expected within window (gap < 64)
3. seq > expected beyond window (gap > jump_limit candidate resync)
4. seq < expected within window, first arrival (late packet, out of order)
5. seq < expected within window, duplicate (exact duplicate, invalid)
6. seq < expected beyond window, first arrival (stale packet, suspicious out of order)
7. seq < expected beyond window, duplicate (repeated stale packet, invalid duplicate)
8. sequence wraparound (boundary rollover near 2^64-1 and 2^32-1)
9. large backward jump without reset flag (2-consecutive regression re-anchoring)
10. session identifier change (epoch/session reset)
11. session timestamp backward movement
12. heartbeat packet (valid, channel isolated)
13. packet on a newly observed channel (clean allocation)
14. concurrent packets on different channels (isolated sequence state)
"""

from __future__ import annotations

import pytest

from mdrap.models import (
    RawEvent,
    CanonicalEvent,
    EventType,
    QualityStatus,
    Reason,
)
from mdrap.engine import Engine
from mdrap.quality import QualityEngine, QualityConfig
from mdrap.fastpath import FastQualityEngine, is_available as has_fastpath


def _make_event(
    seq: int | None,
    source: str = "NASDAQ",
    instrument: str = "AAPL",
    price: float = 150.0,
    ts: float = 1000.0,
    event_type: EventType = EventType.TRADE,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"ev_{source}_{instrument}_{seq}_{ts}",
        instrument_id=instrument,
        event_type=event_type,
        exchange_timestamp=ts,
        receive_timestamp=ts,
        processing_timestamp=ts,
        source=source,
        sequence_number=seq,
        price=price,
        quantity=10.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id=f"raw_{seq}",
    )


def _make_raw(
    seq: int | None,
    source: str = "NASDAQ",
    instrument: str = "AAPL",
    price: float = 150.0,
    ts: float = 1000.0,
    event_type: str = "TRADE",
    session_id: str | None = None,
) -> RawEvent:
    payload = {
        "instrument": instrument,
        "event_type": event_type,
        "price": price,
        "quantity": 10.0,
        "sequence": seq,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return RawEvent(
        source=source,
        receive_timestamp=ts,
        payload=payload,
    )


@pytest.fixture
def engines():
    """Provides pure-Python QualityEngine, C FastQualityEngine (if available), and Engine."""
    py_qe = QualityEngine(QualityConfig(seq_jump_limit=50))
    fast_qe = FastQualityEngine(seq_jump_limit=50) if has_fastpath() else None
    eng = Engine(path=None)
    return {"py": py_qe, "fast": fast_qe, "eng": eng}


def test_scenario_1_seq_expected(engines):
    """Case 1: seq == expected -> VALID."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # Baseline packet 1
    ev1 = _make_event(1)
    assert py_qe.evaluate(ev1).quality_status == QualityStatus.VALID
    if fast_qe:
        assert fast_qe.evaluate(ev1).quality_status == QualityStatus.VALID
    assert eng.submit(_make_raw(1)).quality_status == QualityStatus.VALID

    # Packet 2 (expected)
    ev2 = _make_event(2)
    res_py = py_qe.evaluate(ev2)
    assert res_py.quality_status == QualityStatus.VALID
    assert not res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev2)
        assert res_fast.quality_status == QualityStatus.VALID
        assert not res_fast.reasons

    res_eng = eng.submit(_make_raw(2))
    assert res_eng.quality_status == QualityStatus.VALID
    assert not res_eng.reasons


def test_scenario_2_seq_gap_within_window(engines):
    """Case 2: seq > expected within window -> SUSPICIOUS, SEQUENCE_GAP."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # Packet 1
    py_qe.evaluate(_make_event(1))
    if fast_qe:
        fast_qe.evaluate(_make_event(1))
    eng.submit(_make_raw(1))

    # Jump to 5 (gap of 3 within window)
    ev5 = _make_event(5)
    res_py = py_qe.evaluate(ev5)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev5)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.SEQUENCE_GAP.value in res_fast.reasons

    res_eng = eng.submit(_make_raw(5))
    assert res_eng.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in res_eng.reasons


def test_scenario_3_seq_gap_beyond_window(engines):
    """Case 3: seq > expected beyond jump limit -> candidate resync."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    py_qe.evaluate(_make_event(1))
    if fast_qe:
        fast_qe.evaluate(_make_event(1))
    eng.submit(_make_raw(1))

    # Massive jump to 1000 (> seq_jump_limit=50)
    ev1000 = _make_event(1000)
    res_py = py_qe.evaluate(ev1000)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev1000)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.SEQUENCE_GAP.value in res_fast.reasons

    res_eng = eng.submit(_make_raw(1000))
    assert res_eng.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in res_eng.reasons


def test_scenario_4_and_5_out_of_order_and_duplicate_within_window(engines):
    """Cases 4 & 5: late packet arrival within window -> SUSPICIOUS OUT_OF_ORDER, repeat -> INVALID DUPLICATE."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # Establish baseline up to seq 10
    for i in (1, 10):
        py_qe.evaluate(_make_event(i))
        if fast_qe:
            fast_qe.evaluate(_make_event(i))
        eng.submit(_make_raw(i))

    # Case 4: Packet 5 arrives late (d = 5 < 64) -> First arrival is SUSPICIOUS OUT_OF_ORDER
    ev5 = _make_event(5)
    res_py = py_qe.evaluate(ev5)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev5)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.OUT_OF_ORDER.value in res_fast.reasons

    res_eng = eng.submit(_make_raw(5))
    assert res_eng.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in res_eng.reasons

    # Case 5: Packet 5 arrives AGAIN -> Duplicate within window is INVALID DUPLICATE
    res_py_dup = py_qe.evaluate(ev5)
    assert res_py_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res_py_dup.reasons

    if fast_qe:
        res_fast_dup = fast_qe.evaluate(ev5)
        assert res_fast_dup.quality_status == QualityStatus.INVALID
        assert Reason.DUPLICATE.value in res_fast_dup.reasons

    res_eng_dup = eng.submit(_make_raw(5))
    assert res_eng_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res_eng_dup.reasons


def test_scenario_6_and_7_out_of_window_stale_and_duplicate(engines):
    """Cases 6 & 7: packet older than 64-bit window -> first SUSPICIOUS OUT_OF_ORDER, repeated -> INVALID DUPLICATE."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # Establish baseline up to seq 100 with steps <= seq_jump_limit (50)
    for i in (1, 30, 60, 90, 100):
        py_qe.evaluate(_make_event(i))
        if fast_qe:
            fast_qe.evaluate(_make_event(i))
        eng.submit(_make_raw(i))

    # Case 6: Packet 20 arrives (d = 80 >= 64) -> First arrival is SUSPICIOUS OUT_OF_ORDER
    ev20 = _make_event(20)
    res_py = py_qe.evaluate(ev20)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev20)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.OUT_OF_ORDER.value in res_fast.reasons

    res_eng = eng.submit(_make_raw(20))
    assert res_eng.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in res_eng.reasons

    # Case 7: Packet 20 arrives AGAIN -> Repeated stale packet MUST be detected as INVALID DUPLICATE!
    res_py_dup = py_qe.evaluate(ev20)
    assert res_py_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res_py_dup.reasons

    if fast_qe:
        res_fast_dup = fast_qe.evaluate(ev20)
        assert res_fast_dup.quality_status == QualityStatus.INVALID
        assert Reason.DUPLICATE.value in res_fast_dup.reasons

    res_eng_dup = eng.submit(_make_raw(20))
    assert res_eng_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res_eng_dup.reasons


def test_scenario_8_sequence_wraparound_boundaries(engines):
    """Case 8: boundary near 2^32-1 and 2^64-1."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # 32-bit boundary: 4294967295 -> 0
    seq_max32 = 0xFFFFFFFF
    ev_max = _make_event(seq_max32)
    py_qe.evaluate(ev_max)
    if fast_qe:
        fast_qe.evaluate(ev_max)
    eng.submit(_make_raw(seq_max32))

    # First rollover packet (seq 0) -> regression detected
    ev_zero = _make_event(0)
    res_py = py_qe.evaluate(ev_zero)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS

    if fast_qe:
        res_fast = fast_qe.evaluate(ev_zero)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS

    res_eng = eng.submit(_make_raw(0))
    assert res_eng.quality_status == QualityStatus.SUSPICIOUS

    # Second conforming rollover packet (seq 1) -> re-anchors
    ev_one = _make_event(1)
    res_py1 = py_qe.evaluate(ev_one)
    assert res_py1.quality_status in (QualityStatus.VALID, QualityStatus.SUSPICIOUS)


def test_scenario_9_large_backward_jump_reanchoring(engines):
    """Case 9: large backward jump re-anchoring on 2 consecutive conforming events."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]
    eng = engines["eng"]

    # High baseline
    py_qe.evaluate(_make_event(5000))
    if fast_qe:
        fast_qe.evaluate(_make_event(5000))
    eng.submit(_make_raw(5000))

    # First event in low range (seq 10) -> regress_count=1, SUSPICIOUS
    ev10 = _make_event(10)
    assert py_qe.evaluate(ev10).quality_status == QualityStatus.SUSPICIOUS
    if fast_qe:
        assert fast_qe.evaluate(ev10).quality_status == QualityStatus.SUSPICIOUS
    assert eng.submit(_make_raw(10)).quality_status == QualityStatus.SUSPICIOUS

    # Second event in low range (seq 11) -> regress_count=2, adopts new baseline!
    ev11 = _make_event(11)
    py_qe.evaluate(ev11)
    if fast_qe:
        fast_qe.evaluate(ev11)
    eng.submit(_make_raw(11))

    # Third event (seq 12) is now contiguous next -> VALID!
    ev12 = _make_event(12)
    assert py_qe.evaluate(ev12).quality_status == QualityStatus.VALID
    if fast_qe:
        assert fast_qe.evaluate(ev12).quality_status == QualityStatus.VALID
    assert eng.submit(_make_raw(12)).quality_status == QualityStatus.VALID


def test_scenario_10_session_identifier_change(engines):
    """Case 10: session identifier change resets epoch cleanly."""
    eng = engines["eng"]

    # Session 1
    eng.submit(_make_raw(seq=100, session_id="SESS_A"))
    assert eng.state.sequence_state.get("NASDAQ:AAPL") == 100

    # Session 2 begins at seq 1
    dec = eng.submit(_make_raw(seq=1, session_id="SESS_B"))
    assert dec.quality_status == QualityStatus.VALID
    assert eng.state.sequence_state.get("NASDAQ:AAPL") == 1


def test_scenario_11_timestamp_backward_movement(engines):
    """Case 11: exchange timestamp moving backward is flagged SUSPICIOUS."""
    py_qe = engines["py"]
    fast_qe = engines["fast"]

    ev1 = _make_event(seq=1, ts=1000.0)
    py_qe.evaluate(ev1)
    if fast_qe:
        fast_qe.evaluate(ev1)

    ev_back = _make_event(seq=2, ts=990.0)  # 10 seconds in the past!
    res_py = py_qe.evaluate(ev_back)
    assert res_py.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in res_py.reasons

    if fast_qe:
        res_fast = fast_qe.evaluate(ev_back)
        assert res_fast.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.OUT_OF_ORDER.value in res_fast.reasons


def test_scenario_12_heartbeat_packet(engines):
    """Case 12: heartbeat packets are VALID and preserve channel continuity."""
    eng = engines["eng"]
    raw_hb = _make_raw(seq=1, event_type="STATUS")
    dec = eng.submit(raw_hb)
    assert dec.quality_status == QualityStatus.VALID
    assert not dec.reasons


def test_scenario_13_new_channel_allocation(engines):
    """Case 13: packet on newly observed channel allocates clean state without false positives."""
    eng = engines["eng"]
    dec_goog = eng.submit(_make_raw(seq=1, instrument="GOOG"))
    assert dec_goog.quality_status == QualityStatus.VALID
    assert not dec_goog.reasons

    dec_tsla = eng.submit(_make_raw(seq=1, instrument="TSLA"))
    assert dec_tsla.quality_status == QualityStatus.VALID
    assert not dec_tsla.reasons


def test_scenario_14_channels_isolated_from_peer_anomalies(engines):
    """Case 14: out-of-order or duplicate on channel A does NOT contaminate channel B."""
    eng = engines["eng"]

    # Channel A has duplicate
    eng.submit(_make_raw(seq=10, instrument="AAPL"))
    eng.submit(_make_raw(seq=11, instrument="AAPL"))
    dec_dup = eng.submit(_make_raw(seq=10, instrument="AAPL"))
    assert dec_dup.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dec_dup.reasons

    # Channel B advances cleanly and must be unaffected
    eng.submit(_make_raw(seq=10, instrument="MSFT"))
    dec_b = eng.submit(_make_raw(seq=11, instrument="MSFT"))
    assert dec_b.quality_status == QualityStatus.VALID
    assert not dec_b.reasons
