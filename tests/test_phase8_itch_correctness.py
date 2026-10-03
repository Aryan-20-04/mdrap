import time
import pytest
from itch import (
    ITCHParser,
    ITCHOrderBookTracker,
    STRUCT_E,
    STRUCT_C,
    STRUCT_R,
    MSG_ORDER_EXECUTED,
    MSG_ORDER_EXECUTED_PRICE,
    MSG_STOCK_DIRECTORY,
    PRICE_FACTOR_ITCH,
)
from quality import QualityEngine
from models import QualityStatus, Reason


def test_itch_untracked_order_execution_synthesis():
    """Verify ITCH executions for untracked orders are NOT silently dropped (CORR-01)."""
    tracker = ITCHOrderBookTracker()
    ts_b = (34_200_000_000_000).to_bytes(6, "big")  # 09:30:00

    # Register locate 123 -> TSLA via Stock Directory 'R' message
    r_payload = STRUCT_R.pack(
        123, 0, ts_b, b"TSLA    ", b"Q", b"N", 100, b"N", b"C", b"  ", b"Y", b"N", b"N", b"N", b"N", 1, b"N"
    )
    r_msg = ITCHParser.parse_payload(MSG_STOCK_DIRECTORY, r_payload)
    assert r_msg is not None
    tracker.process_message(r_msg)

    # Now execute an untracked order ref 999999 (e.g. order arrived before gateway startup)
    pe = STRUCT_E.pack(123, 0, ts_b, 999999, 150, 88888)
    msg_e = ITCHParser.parse_payload(MSG_ORDER_EXECUTED, pe)
    event = tracker.process_message(msg_e)

    assert event is not None, "Execution for untracked order was silently dropped!"
    assert event.instrument_id == "TSLA"
    assert event.quantity == 150.0
    assert event.quality_status == QualityStatus.SUSPICIOUS
    assert "EXECUTION_WITHOUT_LOCAL_ORDER_STATE" in event.reasons

    # Execute untracked order with explicit price (C message)
    pc = STRUCT_C.pack(123, 0, ts_b, 999998, 200, 88889, b"Y", int(250.75 * PRICE_FACTOR_ITCH))
    msg_c = ITCHParser.parse_payload(MSG_ORDER_EXECUTED_PRICE, pc)
    event_c = tracker.process_message(msg_c)

    assert event_c is not None, "Execution with price was silently dropped!"
    assert event_c.instrument_id == "TSLA"
    assert event_c.quantity == 200.0
    assert event_c.price == pytest.approx(250.75)
    assert event_c.quality_status == QualityStatus.VALID


def test_itch_epoch_timestamp_avoids_stale_quarantine():
    """Verify ITCH timestamps convert to UTC epoch avoiding 100% false-positive staleness quarantine (CORR-05)."""
    t_now = time.time()
    today_midnight = float((int(t_now) // 86400) * 86400)
    current_secs_into_day = t_now - today_midnight

    # Construct ITCH timestamp near current seconds of the day
    ts_ns = int(current_secs_into_day * 1e9)
    ts_b = ts_ns.to_bytes(6, "big")

    tracker = ITCHOrderBookTracker(session_midnight_epoch=today_midnight)

    # Register locate
    r_payload = STRUCT_R.pack(
        10, 0, ts_b, b"AAPL    ", b"Q", b"N", 100, b"N", b"C", b"  ", b"Y", b"N", b"N", b"N", b"N", 1, b"N"
    )
    tracker.process_message(ITCHParser.parse_payload(MSG_STOCK_DIRECTORY, r_payload))

    # Untracked execution with price
    pc = STRUCT_C.pack(10, 0, ts_b, 5555, 100, 9999, b"Y", int(175.50 * PRICE_FACTOR_ITCH))
    msg_c = ITCHParser.parse_payload(MSG_ORDER_EXECUTED_PRICE, pc)
    event = tracker.process_message(msg_c)

    assert event is not None
    # Timestamp should be in UTC epoch (> 1.7e9)
    assert event.exchange_timestamp > 1.7e9
    assert abs(event.exchange_timestamp - t_now) < 5.0

    # Pass through QualityEngine - must NOT be flagged as STALE_TIMESTAMP
    from quality import QualityConfig
    qe = QualityEngine(QualityConfig(staleness_threshold_s=10.0))
    result = qe.evaluate(event)

    assert result.quality_status != QualityStatus.INVALID
    assert Reason.STALE.value not in result.reasons
