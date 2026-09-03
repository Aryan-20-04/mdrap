import os
import sys
import tempfile
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from service import MarketDataDaemon, StreamClient


@pytest.fixture
def running_daemon():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # Use a high test port to avoid conflict
    port = 19876
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=port,
        db_path=db_path,
        use_live=False,
        sim_speed_eps=5000.0,
    )
    daemon.start(blocking=False)
    # Wait for socket listener to bind
    time.sleep(0.3)
    yield daemon
    daemon.stop()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_daemon_status_query(running_daemon):
    client = StreamClient(host="127.0.0.1", port=running_daemon.port)
    client.connect()
    st = client.get_status()
    assert isinstance(st, dict)
    assert "uptime_s" in st
    assert st["port"] == running_daemon.port
    assert st["active_clients"] >= 1
    client.close()


def test_client_subscribe_and_stream_ticks(running_daemon):
    client = StreamClient(host="127.0.0.1", port=running_daemon.port)
    client.connect()

    ticks = list(client.stream(symbol="ALL", limit=5))
    assert len(ticks) == 5
    for t in ticks:
        assert t.get("type") == "TICK"
        assert "sym" in t
        assert "status" in t
        assert "proc_us" in t
    client.close()


def test_client_bbo_query(running_daemon):
    client = StreamClient(host="127.0.0.1", port=running_daemon.port)
    client.connect()
    # Let daemon ingest some ticks
    time.sleep(0.2)
    bbo = client.get_bbo("AAPL")
    # bbo may be None if no quotes yet, or dict if observed
    if bbo is not None:
        assert "bid" in bbo
        assert "ask" in bbo
    client.close()


def test_client_abrupt_disconnect(running_daemon):
    # Connect 3 clients and immediately disconnect 2
    c1 = StreamClient(host="127.0.0.1", port=running_daemon.port)
    c2 = StreamClient(host="127.0.0.1", port=running_daemon.port)
    c3 = StreamClient(host="127.0.0.1", port=running_daemon.port)

    c1.connect()
    c2.connect()
    c3.connect()

    # Abrupt socket close
    c1.sock.close()
    c2.sock.close()

    # Third client should continue streaming normally
    ticks = list(c3.stream(symbol="ALL", limit=3))
    assert len(ticks) == 3
    c3.close()
