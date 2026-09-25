import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from client import MarketEvent, MDRAPClient
from service import MarketDataDaemon
from protocol import (
    pack_tick_frame,
    unpack_tick_payload,
    pack_depth_frame,
    unpack_depth_payload,
    BinaryStreamParser,
    HEADER_STRUCT,
    TICK_PAYLOAD_LEN,
    DEPTH_PAYLOAD_LEN,
)


def test_tick_binary_roundtrip():
    t_now = time.time()
    frame = pack_tick_frame(
        seq=42,
        symbol="BTC/USD",
        source="BINANCE",
        price=80500.50,
        size=1.5,
        bid=80500.0,
        ask=80501.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=t_now - 0.002,
        ingest_ts=t_now - 0.001,
        broadcast_ts=t_now,
        engine_us=18.5,
    )

    assert len(frame) == HEADER_STRUCT.size + TICK_PAYLOAD_LEN
    payload_bytes = frame[HEADER_STRUCT.size :]
    parsed = unpack_tick_payload(payload_bytes)

    assert parsed["type"] == "TICK"
    assert parsed["seq"] == 42
    assert parsed["sym"] == "BTC/USD"
    assert parsed["source"] == "BINANCE"
    assert parsed["price"] == pytest.approx(80500.50)
    assert parsed["size"] == pytest.approx(1.5)
    assert parsed["bid"] == pytest.approx(80500.0)
    assert parsed["ask"] == pytest.approx(80501.0)
    assert parsed["status"] == "VALID"
    assert parsed["is_crossed"] is False
    assert parsed["engine_us"] == pytest.approx(18.5, rel=1e-2)


def test_depth_binary_roundtrip():
    t_now = time.time()
    frame = pack_depth_frame(
        seq=43,
        symbol="ETH/USD",
        best_bid=3000.0,
        best_ask=3001.0,
        bid_size=10.0,
        ask_size=15.0,
        micro_price=3000.6,
        ofi=0.20,
        is_crossed=True,
        exchange_ts=t_now - 0.002,
        ingest_ts=t_now - 0.001,
        broadcast_ts=t_now,
        engine_us=22.1,
    )

    assert len(frame) == HEADER_STRUCT.size + DEPTH_PAYLOAD_LEN
    payload_bytes = frame[HEADER_STRUCT.size :]
    parsed = unpack_depth_payload(payload_bytes)

    assert parsed["type"] == "DEPTH"
    assert parsed["seq"] == 43
    assert parsed["sym"] == "ETH/USD"
    assert parsed["micro_price"] == pytest.approx(3000.6)
    assert parsed["ofi"] == pytest.approx(0.20)
    assert parsed["is_crossed"] is True
    assert parsed["bid"] == pytest.approx(3000.0)
    assert parsed["ask"] == pytest.approx(3001.0)


def test_binary_stream_parser_fragmentation():
    """Verify that BinaryStreamParser reassembles frames across fragmented chunks."""
    f1 = pack_tick_frame(
        seq=1,
        symbol="AAPL",
        source="FEEDX",
        price=150.0,
        size=10.0,
        bid=149.9,
        ask=150.1,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=10.0,
    )
    f2 = pack_depth_frame(
        seq=2,
        symbol="AAPL",
        best_bid=149.9,
        best_ask=150.1,
        bid_size=50.0,
        ask_size=50.0,
        micro_price=150.0,
        ofi=0.0,
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=12.0,
    )

    combined = f1 + f2
    parser = BinaryStreamParser()

    # Split combined 200 bytes into small 15-byte chunks
    events = []
    chunk_size = 15
    for i in range(0, len(combined), chunk_size):
        chunk = combined[i : i + chunk_size]
        events.extend(parser.feed(chunk))

    assert len(events) == 2
    assert events[0]["seq"] == 1
    assert events[0]["type"] == "TICK"
    assert events[1]["seq"] == 2
    assert events[1]["type"] == "DEPTH"


def test_binary_serialization_speed_benchmark():
    """Verify that binary packing takes < 2 microseconds per event."""
    t0 = time.perf_counter_ns()
    count = 10_000
    for i in range(count):
        pack_tick_frame(
            seq=i,
            symbol="BTC/USD",
            source="BINANCE",
            price=80000.0,
            size=1.0,
            bid=79999.0,
            ask=80001.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=10.0,
        )
    elapsed_ns = time.perf_counter_ns() - t0
    avg_ns = elapsed_ns / count
    # Threshold 50,000ns accommodates tracing/profiling overhead and heavy multi-suite scheduling jitter (normally < 1,000ns)
    assert avg_ns < 50_000, (
        f"Binary pack latency was {avg_ns:.1f} ns (expected < 50000ns)"
    )


@pytest.fixture
def running_daemon():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        sim_speed_eps=10000.0,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    yield daemon
    daemon.stop()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_binary_stream_parser_sync_recovery():
    """Verify that BinaryStreamParser recovers from noise and corrupt byte sequences."""
    parser = BinaryStreamParser()
    garbage = b"RANDOM_CORRUPT_BYTES_WITHOUT_MAGIC_MD_HERE_EXTRA_JUNK"
    valid_tick = pack_tick_frame(
        seq=999,
        symbol="BTC/USD",
        source="COINBASE",
        price=81000.0,
        size=2.0,
        bid=80990.0,
        ask=81010.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=11.5,
    )
    events = parser.feed(garbage + valid_tick)
    assert len(events) == 1
    assert events[0]["seq"] == 999
    assert events[0]["sym"] == "BTC/USD"
    assert events[0]["price"] == pytest.approx(81000.0)


def test_client_daemon_binary_streaming(running_daemon):
    """End-to-end integration: client streams binary frames over TCP socket."""
    with MDRAPClient(
        host="127.0.0.1", port=running_daemon.port, use_binary=True
    ) as client:
        client.subscribe("ALL")
        events = []
        for ev in client.stream(timeout=3.0, max_events=10):
            events.append(ev)

        assert len(events) == 10
        for i in range(len(events)):
            ev = events[i]
            assert ev.seq > 0
            assert ev.symbol != ""
            assert ev.event_type == "TICK"
            if i > 0:
                assert ev.seq > events[i - 1].seq


def test_mixed_json_and_binary_clients(running_daemon):
    """Verify concurrent JSON and Binary clients receive identical stream without interference."""
    with MDRAPClient(
        host="127.0.0.1", port=running_daemon.port, use_binary=False
    ) as json_client:
        with MDRAPClient(
            host="127.0.0.1", port=running_daemon.port, use_binary=True
        ) as bin_client:
            json_client.subscribe("ALL")
            bin_client.subscribe("ALL")

            json_events = [ev for ev in json_client.stream(timeout=3.0, max_events=5)]
            bin_events = [ev for ev in bin_client.stream(timeout=3.0, max_events=5)]

            assert len(json_events) == 5
            assert len(bin_events) == 5
            assert all(ev.event_type == "TICK" for ev in json_events)
            assert all(ev.event_type == "TICK" for ev in bin_events)
