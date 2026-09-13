import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shm import (
    SHMWriter, SHMReader, MAGIC, VERSION,
    EVENT_TYPE_TICK, EVENT_TYPE_DEPTH, HAS_SHM
)


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_writer_and_reader_lifecycle():
    shm_name = "test_mdrap_lifecycle"
    writer = SHMWriter(name=shm_name, slot_count=256)
    assert writer.shm is not None

    reader = SHMReader(name=shm_name)
    assert reader.slot_count == 256
    assert reader.read_latest_seq() == 0

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_tick_write_and_read():
    shm_name = "test_mdrap_tick"
    writer = SHMWriter(name=shm_name, slot_count=256)
    reader = SHMReader(name=shm_name)

    t_now = time.time()
    writer.write_tick(
        seq=1,
        symbol="BTC/USD",
        source="BINANCE",
        price=80500.50,
        size=1.25,
        bid=80500.0,
        ask=80501.0,
        bid_size=2.0,
        ask_size=3.5,
        status="VALID",
        is_crossed=False,
        exchange_ts=t_now - 0.002,
        ingest_ts=t_now - 0.001,
        broadcast_ts=t_now,
        engine_us=18.5,
    )

    assert reader.read_latest_seq() == 1
    event = reader.read_slot(1)
    assert event is not None
    assert event["type"] == "TICK"
    assert event["seq"] == 1
    assert event["sym"] == "BTC/USD"
    assert event["source"] == "BINANCE"
    assert event["price"] == pytest.approx(80500.50)
    assert event["size"] == pytest.approx(1.25)
    assert event["bid"] == pytest.approx(80500.0)
    assert event["ask"] == pytest.approx(80501.0)
    assert event["status"] == "VALID"
    assert not event["is_crossed"]
    assert event["engine_us"] == pytest.approx(18.5, rel=1e-2)

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_depth_write_and_read():
    shm_name = "test_mdrap_depth"
    writer = SHMWriter(name=shm_name, slot_count=256)
    reader = SHMReader(name=shm_name)

    t_now = time.time()
    writer.write_depth(
        seq=2,
        symbol="ETH/USD",
        best_bid=3000.0,
        best_ask=3001.0,
        bid_size=10.0,
        ask_size=12.0,
        micro_price=3000.45,
        ofi=0.09,
        is_crossed=True,
        exchange_ts=t_now - 0.002,
        ingest_ts=t_now - 0.001,
        broadcast_ts=t_now,
        engine_us=22.3,
    )

    assert reader.read_latest_seq() == 2
    event = reader.read_slot(2)
    assert event is not None
    assert event["type"] == "DEPTH"
    assert event["seq"] == 2
    assert event["sym"] == "ETH/USD"
    assert event["micro_price"] == pytest.approx(3000.45)
    assert event["ofi"] == pytest.approx(0.09)
    assert event["is_crossed"] is True

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_ring_buffer_wraparound():
    """Verify that writing 1,000 slots into a 128-slot buffer wraps around correctly."""
    shm_name = "test_mdrap_wrap"
    writer = SHMWriter(name=shm_name, slot_count=128)
    reader = SHMReader(name=shm_name)

    for i in range(1, 1001):
        writer.write_tick(
            seq=i,
            symbol="SOL/USD",
            source="OKX",
            price=150.0 + (i * 0.01),
            size=10.0,
            bid=149.9,
            ask=150.1,
            bid_size=5.0,
            ask_size=5.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=15.0,
        )

    assert reader.read_latest_seq() == 1000

    # Old sequence (e.g. seq 1) has been overwritten by wrap-around
    assert reader.read_slot(1) is None

    # Recent sequences (within last 128 slots) are fully readable
    for s in range(1000 - 120, 1001):
        ev = reader.read_slot(s)
        assert ev is not None
        assert ev["seq"] == s
        assert ev["sym"] == "SOL/USD"

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_shm_stream_generator_sub_microsecond():
    shm_name = "test_mdrap_stream"
    writer = SHMWriter(name=shm_name, slot_count=512)
    reader = SHMReader(name=shm_name)

    # Pre-write 50 events
    for i in range(1, 51):
        writer.write_tick(
            seq=i,
            symbol="AAPL",
            source="FEEDX",
            price=150.0,
            size=100.0,
            bid=149.9,
            ask=150.1,
            bid_size=50.0,
            ask_size=50.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0,
            ingest_ts=1000.001,
            broadcast_ts=1000.002,
            engine_us=12.0,
        )

    events = []
    t0 = time.perf_counter_ns()
    for ev in reader.stream(start_seq=1, max_events=50):
        events.append(ev)
    elapsed_ns = time.perf_counter_ns() - t0

    assert len(events) == 50
    avg_ns_per_read = elapsed_ns / 50.0
    # Average read latency per slot should be well under 50 microseconds (typically < 1-5µs)
    assert avg_ns_per_read < 50_000, f"Read latency was {avg_ns_per_read} ns"

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available")
def test_mdrap_client_with_shm_end_to_end():
    import tempfile
    from service import MarketDataDaemon
    from client import MDRAPClient

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shm_name = "test_e2e_shm"

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=39876,
        db_path=db_path,
        use_live=False,
        sim_speed_eps=10000.0,
        enable_shm=True,
        shm_name=shm_name,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)

    client = MDRAPClient(
        host="127.0.0.1",
        port=39876,
        use_shm=True,
        shm_name=shm_name,
    )
    client.connect()
    assert client.is_connected()
    assert client.shm_reader is not None

    events = []
    for ev in client.stream(timeout=2.0, max_events=10):
        events.append(ev)

    assert len(events) == 10
    for ev in events:
        assert ev.seq > 0
        assert ev.symbol != ""
        assert ev.status in ("VALID", "SUSPICIOUS", "INVALID")

    stats = client.stats()
    assert stats["events_received"] >= 10

    client.close()
    daemon.stop()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
