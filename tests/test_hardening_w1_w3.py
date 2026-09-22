import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ws_feed import (
    parse_binance_frame,
    parse_coinbase_frame,
    parse_okx_frame,
    parse_bybit_frame,
    parse_kraken_frame,
    RUN_ID,
)


def test_w1_genuine_timestamps():
    """W1: Parsers must extract genuine venue timestamps and never fabricate exchange_ts."""
    # Coinbase ticker with ISO-8601 time
    cb_msg = {
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "50000.0",
        "best_bid": "49999.0",
        "best_ask": "50001.0",
        "time": "2024-01-15T12:00:00.000000Z",
        "sequence": 123456,
    }
    raw = parse_coinbase_frame(cb_msg, "BTC/USD")
    assert raw is not None
    # 2024-01-15T12:00:00Z = 1705320000.0
    assert raw.payload["exchange_ts"] == pytest.approx(1705320000.0)

    # OKX books5 with ms timestamp
    okx_msg = {
        "data": [{
            "ts": "1705320000123",
            "seqId": "987654",
            "bids": [["49999.0", "1.5"]],
            "asks": [["50001.0", "2.0"]],
        }]
    }
    raw = parse_okx_frame(okx_msg, "BTC/USD")
    assert raw is not None
    assert raw.payload["exchange_ts"] == pytest.approx(1705320000.123)

    # Binance bookTicker without timestamp
    bn_msg = {"b": "49999.0", "a": "50001.0", "u": 400}
    raw = parse_binance_frame(bn_msg, "BTC/USD")
    assert raw is not None
    assert raw.payload.get("exchange_ts") is None


def test_w2_no_global_sequence_counter():
    """W2: Sequence numbers must come from venue or be None, not a global itertools.count."""
    # Two frames from different venues must not increment each other's sequence
    cb_msg = {
        "type": "ticker",
        "best_bid": "100.0",
        "best_ask": "101.0",
        "sequence": 42,
    }
    raw_cb = parse_coinbase_frame(cb_msg, "TEST")
    assert raw_cb is not None
    assert raw_cb.payload["sequence"] == 42

    bn_msg = {"b": "100.0", "a": "101.0", "u": 100}
    raw_bn = parse_binance_frame(bn_msg, "TEST")
    assert raw_bn is not None
    assert raw_bn.payload.get("sequence") is None


def test_w3_raw_id_scoped_with_run_id():
    """W3: raw_id must be scoped with RUN_ID to prevent cross-run collisions."""
    bn_msg = {"b": "100.0", "a": "101.0"}
    raw = parse_binance_frame(bn_msg, "BTC/USD")
    assert raw is not None
    assert RUN_ID in raw.raw_id


def test_w4_missing_size_never_defaults_to_one():
    """W4: Missing sizes must be None, never invented as 1.0."""
    bn_msg = {"b": "100.0", "a": "101.0"}  # Missing B and A
    raw = parse_binance_frame(bn_msg, "BTC/USD")
    assert raw is not None
    assert raw.payload["bid_size"] is None
    assert raw.payload["ask_size"] is None
