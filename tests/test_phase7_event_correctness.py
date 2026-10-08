import math
import pytest
import time
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.bbo import BBOEngine, ConsolidatedBBO
from mdrap.depth import ConsolidatedDepthEngine
from mdrap.polygon_feed import (
    parse_polygon_quote,
    parse_polygon_trade,
    parse_polygon_aggregate,
)
from mdrap.databento_feed import decode_dbn_record, SyntheticDBNGenerator


def test_bbo_negative_price_quotes_wti():
    """Verify BBOEngine correctly aggregates and serializes negative bid and ask prices (MIC-07)."""
    engine = BBOEngine()

    # WTI May 2020 contract negative pricing scenario
    q1 = CanonicalEvent(
        event_id="q_nymex_1",
        instrument_id="CL_MAY20",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="NYMEX",
        sequence_number=1,
        bid_price=-38.50,
        bid_size=100.0,
        ask_price=-36.50,
        ask_size=50.0,
        quality_status=QualityStatus.VALID,
    )
    bbo1 = engine.observe(q1)
    assert bbo1 is not None
    assert bbo1.best_bid == pytest.approx(-38.50)
    assert bbo1.best_ask == pytest.approx(-36.50)
    assert bbo1.spread == pytest.approx(2.00)
    assert bbo1.mid_price == pytest.approx(-37.50)

    d = bbo1.to_dict()
    assert d["best_bid"] == pytest.approx(-38.50)
    assert d["best_ask"] == pytest.approx(-36.50)
    assert d["spread"] == pytest.approx(2.00)

    # Competing venue with higher (less negative) bid: -37.80
    q2 = CanonicalEvent(
        event_id="q_ice_1",
        instrument_id="CL_MAY20",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.01,
        receive_timestamp=1000.011,
        processing_timestamp=1000.012,
        source="ICE",
        sequence_number=2,
        bid_price=-37.80,
        bid_size=200.0,
        ask_price=-36.20,
        ask_size=80.0,
        quality_status=QualityStatus.VALID,
    )
    bbo2 = engine.observe(q2)
    assert bbo2 is not None
    # Higher bid chosen (-37.80 > -38.50)
    assert bbo2.best_bid == pytest.approx(-37.80)
    assert bbo2.best_bid_source == "ICE"
    # Tighter ask chosen (-36.50 < -36.20)
    assert bbo2.best_ask == pytest.approx(-36.50)
    assert bbo2.best_ask_source == "NYMEX"


def test_polygon_feed_does_not_fabricate_sequences():
    """Verify Polygon parser does NOT fabricate artificial monotonic sequences when upstream doesn't provide them (CORR-04)."""
    # Quote frame with no 's' sequence
    q_frame = {
        "ev": "Q",
        "sym": "AAPL",
        "bx": "V",
        "bp": 150.25,
        "bs": 10,
        "ax": "Q",
        "ap": 150.28,
        "as": 5,
        "t": 1625000000123,
    }
    raw_q = parse_polygon_quote(q_frame)
    assert raw_q is not None
    assert raw_q.payload["sequence"] is None

    # Trade frame with no 'sequence'
    t_frame = {
        "ev": "T",
        "sym": "MSFT",
        "i": "12345",
        "p": 380.50,
        "s": 200,
        "t": 1625000000500,
    }
    raw_t = parse_polygon_trade(t_frame)
    assert raw_t is not None
    assert raw_t.payload["sequence"] is None

    # Frame with explicit upstream sequence provided
    q_frame_with_seq = dict(q_frame)
    q_frame_with_seq["sequence"] = 98765
    raw_q_seq = parse_polygon_quote(q_frame_with_seq)
    assert raw_q_seq is not None
    assert raw_q_seq.payload["sequence"] == 98765


def test_databento_feed_preserves_zero_sequence_as_none():
    """Verify DBN decoder leaves sequence as None when record has seq == 0 (CORR-04)."""
    gen = SyntheticDBNGenerator()
    # Force generator sequence to 0
    gen.seq = 0
    raw_bytes = gen.encode_mbp1_record(1001, 150.0, 150.1, 10, 10)
    ev, consumed = decode_dbn_record(raw_bytes, 0, gen.resolver)
    assert ev is not None
    assert ev.payload["sequence"] is None

    # When generator has valid sequence > 0
    gen.seq = 42
    raw_bytes_42 = gen.encode_mbp1_record(1001, 150.0, 150.1, 10, 10)
    ev42, _ = decode_dbn_record(raw_bytes_42, 0, gen.resolver)
    assert ev42 is not None
    assert ev42.payload["sequence"] == 42


def test_depth_ofi_cont_kukanov_stoikov_formula():
    """Verify Level-1 OFI correctly measures buying pressure on ask lift and selling pressure on ask cut (MIC-03)."""
    depth_eng = ConsolidatedDepthEngine()

    # Step 1: Initial book (bid 100 sz 10, ask 101 sz 10)
    r1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[100.0, 10.0]],
            "asks": [[101.0, 10.0]],
        },
        receive_timestamp=1000.001,
        raw_id="r1",
    )
    ladder1 = depth_eng.observe(r1)
    assert ladder1.ofi == 0.0

    # Step 2: Buying pressure lifts the ask (ask moves UP from 101 to 102, bid stays 100 sz 10)
    # Cont et al.: delta_w_b = 0, best_ask > prev_ba -> delta_w_a = -prev_bas = -10.
    # OFI = delta_w_b - delta_w_a = 0 - (-10) = +10.0 (Positive OFI = net buying)
    r2 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.01,
            "bids": [[100.0, 10.0]],
            "asks": [[102.0, 5.0]],
        },
        receive_timestamp=1000.011,
        raw_id="r2",
    )
    ladder2 = depth_eng.observe(r2)
    assert ladder2.ofi == pytest.approx(10.0)

    # Step 3: Selling pressure cuts the ask (ask moves DOWN from 102 to 101.5 sz 8)
    # Cont et al.: delta_w_b = 0, best_ask < prev_ba -> delta_w_a = +best_ask_sz = +8.
    # OFI = delta_w_b - delta_w_a = 0 - 8 = -8.0 (Negative OFI = net selling)
    r3 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.02,
            "bids": [[100.0, 10.0]],
            "asks": [[101.5, 8.0]],
        },
        receive_timestamp=1000.021,
        raw_id="r3",
    )
    ladder3 = depth_eng.observe(r3)
    assert ladder3.ofi == pytest.approx(-8.0)
