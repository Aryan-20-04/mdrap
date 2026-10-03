import pytest
import math
import time
from protocol import (
    pack_tick_frame,
    unpack_tick_payload,
    pack_tick_frame_v2,
    unpack_tick_payload_v2,
    pack_depth_frame,
    BinaryStreamParser,
    HEADER_STRUCT,
    TICK_V2_PAYLOAD_LEN,
    MSG_TYPE_TICK_V2,
)


def test_v2_long_option_symbol_differentiation():
    """Verify that 32-byte symbols in V2 preserve full OCC option identifiers."""
    call_sym = "AAPL260116C00150000"
    put_sym = "AAPL260116P00150000"

    # In V1 (8-byte symbol max), both would truncate to "AAPL2601", causing a symbol collision
    v1_call = pack_tick_frame(
        seq=1, symbol=call_sym, source="OPRA", price=12.5, size=10.0,
        bid=12.4, ask=12.6, status="VALID", is_crossed=False,
        exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=5.0
    )
    v1_parsed = unpack_tick_payload(v1_call[HEADER_STRUCT.size:])
    assert v1_parsed["sym"] == "AAPL2601"  # Truncated in V1!

    # In V2 (32-byte symbol), full OCC symbol is preserved
    v2_call = pack_tick_frame_v2(
        seq=101, symbol=call_sym, source="OPRA", price=12.5, size=10.0,
        bid=12.4, ask=12.6, status="VALID", is_crossed=False,
        exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=5.0,
        reason_mask=0
    )
    v2_put = pack_tick_frame_v2(
        seq=102, symbol=put_sym, source="OPRA", price=8.5, size=5.0,
        bid=8.4, ask=8.6, status="VALID", is_crossed=False,
        exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=5.0,
        reason_mask=0
    )

    call_parsed = unpack_tick_payload_v2(v2_call[HEADER_STRUCT.size:])
    put_parsed = unpack_tick_payload_v2(v2_put[HEADER_STRUCT.size:])

    assert call_parsed["sym"] == call_sym
    assert put_parsed["sym"] == put_sym
    assert call_parsed["sym"] != put_parsed["sym"]


def test_negative_price_preservation():
    """Verify negative prices (e.g. WTI -37.63, sub-zero energy) are preserved and not coerced to None."""
    wti_negative = -37.63
    frame_v1 = pack_tick_frame(
        seq=201, symbol="CL_2020", source="NYMEX", price=wti_negative, size=100.0,
        bid=-38.00, ask=-37.00, status="VALID", is_crossed=False,
        exchange_ts=200.0, ingest_ts=200.1, broadcast_ts=200.2, engine_us=4.0
    )
    parsed_v1 = unpack_tick_payload(frame_v1[HEADER_STRUCT.size:])
    assert parsed_v1["price"] == pytest.approx(-37.63)
    assert parsed_v1["bid"] == pytest.approx(-38.00)
    assert parsed_v1["ask"] == pytest.approx(-37.00)

    frame_v2 = pack_tick_frame_v2(
        seq=202, symbol="CL_202005_NEG", source="NYMEX_FUTURES", price=wti_negative, size=50.0,
        bid=-38.00, ask=-37.00, status="VALID", is_crossed=False,
        exchange_ts=200.0, ingest_ts=200.1, broadcast_ts=200.2, engine_us=4.0,
        reason_mask=0
    )
    parsed_v2 = unpack_tick_payload_v2(frame_v2[HEADER_STRUCT.size:])
    assert parsed_v2["price"] == pytest.approx(-37.63)
    assert parsed_v2["bid"] == pytest.approx(-38.00)
    assert parsed_v2["ask"] == pytest.approx(-37.00)


def test_v2_reason_mask_and_extended_source():
    """Verify that 64-bit reason masks and 16-byte source identifiers are faithfully preserved."""
    reason_bits = (1 << 0) | (1 << 5) | (1 << 33) | (1 << 62)
    source_name = "DATABENTO_EQUITY"  # 16 characters

    frame = pack_tick_frame_v2(
        seq=301, symbol="SPY", source=source_name, price=500.25, size=100.0,
        bid=500.20, ask=500.30, status="SUSPICIOUS", is_crossed=False,
        exchange_ts=300.0, ingest_ts=300.1, broadcast_ts=300.2, engine_us=6.5,
        reason_mask=reason_bits
    )
    assert len(frame) == HEADER_STRUCT.size + TICK_V2_PAYLOAD_LEN

    parsed = unpack_tick_payload_v2(frame[HEADER_STRUCT.size:])
    assert parsed["type"] == "TICK"
    assert parsed["version"] == 2
    assert parsed["source"] == source_name
    assert parsed["reason_mask"] == reason_bits
    assert parsed["status"] == "SUSPICIOUS"


def test_stream_parser_mixed_v1_v2_and_depth():
    """Verify BinaryStreamParser seamlessly decodes mixed V1, V2, and Depth frames in a single byte stream."""
    f1 = pack_tick_frame(
        seq=1, symbol="AAPL", source="FEED1", price=150.0, size=10.0,
        bid=149.9, ask=150.1, status="VALID", is_crossed=False,
        exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=5.0
    )
    f2 = pack_tick_frame_v2(
        seq=2, symbol="NVDA250321C00120000", source="OPRA_CHICAGO", price=25.0, size=5.0,
        bid=24.9, ask=25.1, status="VALID", is_crossed=False,
        exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=5.0,
        reason_mask=128
    )
    f3 = pack_depth_frame(
        seq=3, symbol="MSFT", best_bid=400.0, best_ask=400.1,
        bid_size=10.0, ask_size=10.0, micro_price=400.05, ofi=0.0,
        is_crossed=False, exchange_ts=100.0, ingest_ts=100.1, broadcast_ts=100.2, engine_us=4.0
    )

    combined = f1 + f2 + f3
    parser = BinaryStreamParser()

    # Feed in arbitrary slice chunks (e.g. 17 bytes)
    events = []
    chunk_size = 17
    for i in range(0, len(combined), chunk_size):
        chunk = combined[i:i + chunk_size]
        events.extend(parser.feed(chunk))

    assert len(events) == 3
    assert events[0]["seq"] == 1
    assert events[0]["sym"] == "AAPL"

    assert events[1]["seq"] == 2
    assert events[1]["sym"] == "NVDA250321C00120000"
    assert events[1]["version"] == 2
    assert events[1]["source"] == "OPRA_CHICAGO"
    assert events[1]["reason_mask"] == 128

    assert events[2]["seq"] == 3
    assert events[2]["type"] == "DEPTH"
    assert events[2]["sym"] == "MSFT"
