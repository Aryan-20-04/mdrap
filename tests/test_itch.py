"""
Unit tests for NASDAQ TotalView-ITCH 5.0 binary protocol engine and benchmark suite.
"""

import gzip
import os
import struct
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from itch import (
    ITCHParser,
    ITCHOrderBookTracker,
    ITCHSyntheticGenerator,
    ITCHFeedReplayer,
    run_itch_benchmark,
    STRUCT_A,
    STRUCT_F,
    STRUCT_E,
    STRUCT_C,
    STRUCT_X,
    STRUCT_D,
    STRUCT_U,
    STRUCT_P,
    STRUCT_Q,
    STRUCT_S,
    STRUCT_R,
    STRUCT_H,
    STRUCT_FRAME_LEN,
    PRICE_FACTOR_ITCH,
    MSG_ADD_ORDER,
    MSG_ORDER_EXECUTED,
    MSG_ORDER_CANCEL,
    MSG_ORDER_DELETE,
    MSG_ORDER_REPLACE,
)
from models import EventType


def test_itch_parser_add_order():
    ts_b = (34_200_000_000_000).to_bytes(6, "big")
    stock_b = b"AAPL    "
    px_int = int(150.25 * PRICE_FACTOR_ITCH)

    payload = STRUCT_A.pack(1, 0, ts_b, 1001, b"B", 500, stock_b, px_int)
    msg = ITCHParser.parse_payload(MSG_ADD_ORDER, payload)

    assert msg is not None
    assert msg.msg_type == "A"
    assert msg.order_ref == 1001
    assert msg.side == "B"
    assert msg.shares == 500
    assert msg.stock == "AAPL"
    assert msg.price == 150.25
    assert msg.timestamp_ns == 34_200_000_000_000


def test_itch_parser_add_order_mpid():
    ts_b = (34_200_000_000_000).to_bytes(6, "big")
    stock_b = b"NVDA    "
    px_int = int(120.50 * PRICE_FACTOR_ITCH)

    payload = STRUCT_F.pack(1, 0, ts_b, 1002, b"S", 200, stock_b, px_int, b"GSCO")
    msg = ITCHParser.parse_payload(b"F", payload)

    assert msg is not None
    assert msg.msg_type == "F"
    assert msg.order_ref == 1002
    assert msg.side == "S"
    assert msg.shares == 200
    assert msg.stock == "NVDA"
    assert msg.price == 120.50
    assert msg.mpid == "GSCO"


def test_itch_parser_executed():
    ts_b = (34_200_000_000_000).to_bytes(6, "big")
    payload = STRUCT_E.pack(1, 0, ts_b, 1001, 100, 99999)
    msg = ITCHParser.parse_payload(MSG_ORDER_EXECUTED, payload)

    assert msg is not None
    assert msg.msg_type == "E"
    assert msg.order_ref == 1001
    assert msg.shares == 100
    assert msg.match_number == 99999


def test_itch_parser_cancel_and_delete():
    ts_b = (34_200_000_000_000).to_bytes(6, "big")

    # Cancel
    payload_x = STRUCT_X.pack(1, 0, ts_b, 1001, 50)
    msg_x = ITCHParser.parse_payload(MSG_ORDER_CANCEL, payload_x)
    assert msg_x.msg_type == "X"
    assert msg_x.order_ref == 1001
    assert msg_x.shares == 50

    # Delete
    payload_d = STRUCT_D.pack(1, 0, ts_b, 1001)
    msg_d = ITCHParser.parse_payload(MSG_ORDER_DELETE, payload_d)
    assert msg_d.msg_type == "D"
    assert msg_d.order_ref == 1001


def test_itch_parser_replace():
    ts_b = (34_200_000_000_000).to_bytes(6, "big")
    new_px_int = int(151.00 * PRICE_FACTOR_ITCH)

    payload = STRUCT_U.pack(1, 0, ts_b, 1001, 1005, 300, new_px_int)
    msg = ITCHParser.parse_payload(MSG_ORDER_REPLACE, payload)

    assert msg is not None
    assert msg.msg_type == "U"
    assert msg.order_ref == 1001
    assert msg.new_order_ref == 1005
    assert msg.shares == 300
    assert msg.price == 151.00


def test_order_book_tracker_lifecycle():
    book = ITCHOrderBookTracker()
    ts_b = (34_200_000_000_000).to_bytes(6, "big")

    # 1. Add Bid: 500 shares of AAPL @ 150.00
    p1 = STRUCT_A.pack(
        1, 0, ts_b, 101, b"B", 500, b"AAPL    ", int(150.00 * PRICE_FACTOR_ITCH)
    )
    msg1 = ITCHParser.parse_payload(MSG_ADD_ORDER, p1)
    book.process_message(msg1)

    # 2. Add Ask: 300 shares of AAPL @ 150.10
    p2 = STRUCT_A.pack(
        1, 0, ts_b, 102, b"S", 300, b"AAPL    ", int(150.10 * PRICE_FACTOR_ITCH)
    )
    msg2 = ITCHParser.parse_payload(MSG_ADD_ORDER, p2)
    book.process_message(msg2)

    # Check BBO
    bbo = book.get_bbo("AAPL")
    assert bbo["bid"] == 150.00
    assert bbo["ask"] == 150.10
    assert bbo["bid_size"] == 500
    assert bbo["ask_size"] == 300

    # 3. Partial Cancel Bid: Cancel 100 shares of order 101
    px = STRUCT_X.pack(1, 0, ts_b, 101, 100)
    msg_x = ITCHParser.parse_payload(MSG_ORDER_CANCEL, px)
    book.process_message(msg_x)
    assert book.orders[101][3] == 400
    assert book.get_bbo("AAPL")["bid_size"] == 400

    # 4. Execute Ask: 200 shares of order 102
    pe = STRUCT_E.pack(1, 0, ts_b, 102, 200, 7777)
    msg_e = ITCHParser.parse_payload(MSG_ORDER_EXECUTED, pe)
    trade_evt = book.process_message(msg_e)

    assert trade_evt is not None
    assert trade_evt.event_type == EventType.TRADE
    assert trade_evt.instrument_id == "AAPL"
    assert trade_evt.price == 150.10
    assert trade_evt.quantity == 200.0
    assert book.get_bbo("AAPL")["ask_size"] == 100

    # 5. Delete remaining Bid
    pd = STRUCT_D.pack(1, 0, ts_b, 101)
    msg_d = ITCHParser.parse_payload(MSG_ORDER_DELETE, pd)
    book.process_message(msg_d)
    assert 101 not in book.orders
    assert book.get_bbo("AAPL")["bid"] is None


def test_synthetic_generator_and_replayer():
    gen = ITCHSyntheticGenerator(seed=123, symbols=["AAPL", "MSFT"])
    frames = [gen.generate_frame() for _ in range(200)]

    with tempfile.NamedTemporaryFile(suffix=".itch", delete=False) as tmp:
        tmp_path = tmp.name
        for frame in frames:
            tmp.write(frame)

    try:
        replayer = ITCHFeedReplayer(tmp_path)
        messages = list(replayer.iterate_messages())
        assert len(messages) == 200
        assert all(m.msg_type in ("A", "E", "X", "D", "U") for m in messages)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_itch_benchmark_run():
    res = run_itch_benchmark(num_messages=5_000, seed=42, reconstruct_book=True)

    assert res["benchmark"] == "NASDAQ_TOTALVIEW_ITCH_5.0"
    assert res["num_messages"] == 5_000
    assert res["throughput_mps"] > 10_000  # pure python should easily do >10k
    assert res["elapsed_seconds"] > 0
    assert "adds" in res["book_stats"]
