"""Conformance test suite for BaseFeedAdapter implementations using captured wire frames (Item 2.4)."""

import json
import struct
import pytest

from mdrap.adapters import (
    BaseFeedAdapter,
    FeedAdapter,
    ITCHAdapter,
    DatabentoAdapter,
    BinanceAdapter,
    KrakenAdapter,
    AdapterCapabilities,
)
from mdrap.engine import Engine
from mdrap.models import QualityStatus, EventType


def test_protocol_and_capabilities_conformance():
    itch = ITCHAdapter(symbol="AAPL")
    dbn = DatabentoAdapter(symbol="ES.FUT")
    binance = BinanceAdapter(symbol="BTC/USDT")
    kraken = KrakenAdapter(symbol="ETH/USD")

    adapters = [itch, dbn, binance, kraken]
    for ad in adapters:
        assert isinstance(ad, BaseFeedAdapter)
        assert isinstance(ad, FeedAdapter)
        caps = ad.capabilities()
        assert isinstance(caps, AdapterCapabilities)
        assert caps.venue
        assert caps.clock_source in ("EXCHANGE", "GATEWAY_RECV", "HYBRID")


def test_itch_binary_frame_conformance():
    adapter = ITCHAdapter(symbol="AAPL")
    engine = Engine()

    # Captured ITCH Trade Message (P)
    # Struct layout: type(1s), stock_locate(H), tracking(H), timestamp(6s), order_ref(Q), buy_sell(1s), shares(I), stock(8s), price(I), match_num(Q)
    # 9:30:00.123456789 EDT nanoseconds since midnight (34,200,123,456,789 ns)
    ts_ns = 34_200_123_456_789
    ts_bytes = ts_ns.to_bytes(6, "big")
    p_full = struct.pack(
        ">1sHH6sQ1sI8sIQ",
        b"P",
        1,
        2,
        ts_bytes,
        10001,
        b"B",
        500,
        b"AAPL    ",
        1502500,
        99999,
    )
    msg_type = p_full[0:1]
    payload = p_full[1:]

    raw_trade = adapter.unpack_itch_message(msg_type, payload)
    assert raw_trade is not None
    assert raw_trade.source == "NASDAQ_ITCH"
    assert raw_trade.payload["instrument"] == "AAPL"
    assert raw_trade.payload["price"] == 150.25
    assert raw_trade.payload["quantity"] == 500.0

    # Simulate arrival 1ms after exchange matching
    raw_trade.receive_timestamp = raw_trade.payload["exchange_ts"] + 0.001

    # Normalization to CanonicalEvent
    canon = adapter.normalize(raw_trade)
    assert canon.instrument_id == "AAPL"
    assert canon.price == 150.25
    assert canon.event_type == EventType.TRADE

    # Engine step validation
    state, dec = engine.step(engine.state, raw_trade, engine.clock, offset=0)
    assert dec.quality_status == QualityStatus.VALID
    assert dec.canonical_event is not None
    assert dec.canonical_event.event_type == EventType.TRADE


def test_databento_binary_frame_conformance():
    adapter = DatabentoAdapter(symbol="ES.FUT")
    engine = Engine()

    # DBN Header: length(B), rtype(B), publisher_id(H), instrument_id(I), ts_event(Q) -> 16 bytes
    ts_event = 1700000000_000000000
    hdr = struct.pack("<BBHIQ", 12, 0x15, 1, 1001, ts_event)

    # Trade Body: price(q), size(I), action(c), side(c), flags(B), depth(B), ts_recv(Q), ts_in_delta(i), sequence(I) -> 32 bytes
    body = struct.pack(
        "<qIccBBQii", 5000_250_000_000, 20, b"T", b"B", 0, 0, ts_event, 0, 1001
    )
    wire = hdr + body

    raw = adapter.unpack_dbn_frame(wire)
    assert raw is not None
    assert raw.source == "DATABENTO"
    assert raw.payload["instrument"] == "ES.FUT"
    assert raw.payload["price"] == 5000.25
    assert raw.payload["quantity"] == 20.0

    # Simulate arrival 1ms after exchange matching
    raw.receive_timestamp = raw.payload["exchange_ts"] + 0.001

    canon = adapter.normalize(raw)
    assert canon.price == 5000.25
    assert canon.event_type == EventType.TRADE

    state, dec = engine.step(engine.state, raw, engine.clock, offset=0)
    assert dec.quality_status == QualityStatus.VALID
    assert dec.canonical_event is not None


def test_binance_websocket_frame_conformance():
    adapter = BinanceAdapter(symbol="BTC/USDT")
    engine = Engine()

    # Captured Binance bookTicker frame
    binance_frame = {
        "u": 1234567,
        "s": "BTCUSDT",
        "b": "65000.50",
        "B": "0.500",
        "a": "65001.00",
        "A": "1.200",
        "T": 1700000000000,
        "E": 1700000000000,
    }

    raw = adapter.parse_frame(binance_frame)
    assert raw is not None
    assert raw.source == "BINANCE"
    assert raw.payload["instrument"] == "BTC/USDT"
    assert raw.payload["bid"] == 65000.50
    assert raw.payload["ask"] == 65001.00

    # Simulate arrival 1ms after exchange matching
    raw.receive_timestamp = raw.payload["exchange_ts"] + 0.001

    canon = adapter.normalize(raw)
    assert canon.bid_price == 65000.50
    assert canon.ask_price == 65001.00

    state, dec = engine.step(engine.state, raw, engine.clock, offset=0)
    assert dec.quality_status == QualityStatus.VALID


def test_kraken_websocket_frame_conformance():
    adapter = KrakenAdapter(symbol="ETH/USD")
    engine = Engine()

    # Captured Kraken book snapshot frame
    kraken_frame = [
        1001,
        {
            "as": [["2500.50", "1.000", "1700000000.0"]],
            "bs": [["2500.40", "2.000", "1700000000.0"]],
        },
        "book-10",
        "ETH/USD",
        555,
    ]

    raw = adapter.parse_frame(kraken_frame)
    assert raw is not None
    assert raw.source == "KRAKEN"
    assert raw.payload["instrument"] == "ETH/USD"
    assert raw.payload["bid"] == 2500.40
    assert raw.payload["ask"] == 2500.50

    # Simulate arrival 1ms after exchange matching
    raw.receive_timestamp = raw.payload["exchange_ts"] + 0.001

    canon = adapter.normalize(raw)
    assert canon.bid_price == 2500.40
    assert canon.ask_price == 2500.50

    state, dec = engine.step(engine.state, raw, engine.clock, offset=0)
    assert dec.quality_status == QualityStatus.VALID
