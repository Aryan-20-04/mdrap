"""
Unit and Integration Tests for Databento Binary Encoding (DBN) Feed Engine.
"""

import io
import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from databento_feed import (
    decode_dbn_record,
    SymbolResolver,
    SyntheticDBNGenerator,
    DatabentoFeedManager,
    DEFAULT_SYMBOL_MAP,
    RTYPE_MBP_1,
    RTYPE_MBP_10,
    RTYPE_TRADE,
    FIXED_PX_FACTOR,
)
from models import RawEvent


def test_symbol_resolver():
    """Verify numeric ID to ticker symbol resolution."""
    resolver = SymbolResolver({1001: "AAPL", 2001: "ES.c.0"})
    assert resolver.resolve(1001) == "AAPL"
    assert resolver.resolve(2001) == "ES.c.0"
    assert resolver.get_id("AAPL") == 1001

    # Dynamic registration for unmapped ID
    unmapped = resolver.resolve(9999)
    assert unmapped == "DBN_9999"
    assert resolver.resolve(9999) == "DBN_9999"


def test_decode_dbn_mbp1():
    """Verify binary decoding of an 80-byte MBP-1 record."""
    gen = SyntheticDBNGenerator()
    raw_bytes = gen.encode_mbp1_record(
        inst_id=1001,
        bid_px=150.25,
        ask_px=150.28,
        bid_sz=50,
        ask_sz=75,
    )
    assert len(raw_bytes) == 80

    ev, consumed = decode_dbn_record(raw_bytes, offset=0, resolver=gen.resolver)
    assert consumed == 80
    assert ev is not None
    assert isinstance(ev, RawEvent)
    assert ev.source == "DATABENTO-GLBX"
    p = ev.payload
    assert p["instrument"] == "AAPL"
    assert p["event_type"] == "QUOTE"
    assert abs(p["bid"] - 150.25) < 1e-4
    assert abs(p["ask"] - 150.28) < 1e-4
    assert p["bid_size"] == 50.0
    assert p["ask_size"] == 75.0
    assert len(p["bids"]) == 1
    assert len(p["asks"]) == 1


def test_decode_dbn_trade():
    """Verify binary decoding of a 48-byte Trade record."""
    gen = SyntheticDBNGenerator()
    raw_bytes = gen.encode_trade_record(
        inst_id=2001,
        price=5500.50,
        size=15,
        side=b"B",
    )
    assert len(raw_bytes) == 48

    ev, consumed = decode_dbn_record(raw_bytes, offset=0, resolver=gen.resolver)
    assert consumed == 48
    assert ev is not None
    assert isinstance(ev, RawEvent)
    p = ev.payload
    assert p["instrument"] == "ES.c.0"
    assert p["event_type"] == "TRADE"
    assert abs(p["price"] - 5500.50) < 1e-4
    assert p["quantity"] == 15.0
    assert p["side"] == "B"


def test_decode_dbn_mbp10():
    """Verify binary decoding of a 368-byte MBP-10 depth record."""
    gen = SyntheticDBNGenerator()
    raw_bytes = gen.encode_mbp10_record(inst_id=1001, mid_px=150.00)
    assert len(raw_bytes) == 368

    ev, consumed = decode_dbn_record(raw_bytes, offset=0, resolver=gen.resolver)
    assert consumed == 368
    assert ev is not None
    p = ev.payload
    assert p["instrument"] == "AAPL"
    assert p["event_type"] == "QUOTE"
    assert len(p["bids"]) == 10
    assert len(p["asks"]) == 10
    assert p["bid"] > 0
    assert p["ask"] > 0
    assert p["bid"] < p["ask"]


def test_decode_dbn_incomplete_buffer():
    """Verify decoder gracefully handles truncated byte slices."""
    gen = SyntheticDBNGenerator()
    raw_bytes = gen.encode_mbp1_record(1001, 100.0, 101.0, 10, 10)
    # Header truncated (< 16 bytes)
    ev, consumed = decode_dbn_record(raw_bytes[:10])
    assert ev is None
    assert consumed == 0

    # Body truncated (< 80 bytes)
    ev, consumed = decode_dbn_record(raw_bytes[:40])
    assert ev is None
    assert consumed == 0


def test_synthetic_dbn_generator_batch():
    """Verify batch binary stream generation and contiguous record decoding."""
    gen = SyntheticDBNGenerator()
    batch_bytes = gen.generate_random_batch(count=30)
    assert len(batch_bytes) > 0

    offset = 0
    decoded_count = 0
    while offset < len(batch_bytes):
        ev, consumed = decode_dbn_record(batch_bytes, offset, gen.resolver)
        assert consumed > 0
        offset += consumed
        if ev:
            decoded_count += 1

    assert decoded_count == 30


def test_databento_feed_manager_mock():
    """Verify DatabentoFeedManager generates and streams events."""
    mgr = DatabentoFeedManager(
        symbols=["AAPL", "ES.c.0"], mock_mode=True, max_queue_size=100
    )
    assert mgr.mock_mode is True
    mgr.start()
    assert mgr.is_running() is True

    events = []
    for ev in mgr.stream_events(limit=10, timeout_s=2.0):
        events.append(ev)

    mgr.stop()
    assert mgr.is_running() is False
    assert len(events) == 10
    for ev in events:
        assert isinstance(ev, RawEvent)
        assert "DATABENTO" in ev.source

    stats = mgr.stats()
    assert stats["records_decoded"] >= 10
    assert stats["events_emitted"] >= 10


def test_databento_file_replay():
    """Verify historical .dbn binary file reader."""
    gen = SyntheticDBNGenerator()
    batch_bytes = gen.generate_random_batch(count=20)

    with tempfile.NamedTemporaryFile(suffix=".dbn", delete=False) as tf:
        tf.write(batch_bytes)
        temp_path = tf.name

    try:
        mgr = DatabentoFeedManager(file_path=temp_path, mock_mode=False)
        mgr.start()

        events = []
        for ev in mgr.stream_events(limit=20, timeout_s=2.0):
            events.append(ev)

        mgr.stop()
        assert len(events) == 20
        stats = mgr.stats()
        assert stats["records_decoded"] == 20
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
