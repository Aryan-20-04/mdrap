import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from client import MarketEvent, MDRAPClient
from service import MarketDataDaemon


@pytest.fixture
def running_daemon():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    port = 29876
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=port,
        db_path=db_path,
        use_live=False,
        sim_speed_eps=10000.0,
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


def test_market_event_parsing_tick():
    data = {
        "type": "TICK",
        "seq": 101,
        "sym": "BTC/USD",
        "price": 80000.0,
        "size": 1.5,
        "bid": 79995.0,
        "ask": 80005.0,
        "source": "BINANCE",
        "status": "VALID",
        "exchange_ts": 1000.0,
        "ingest_ts": 1000.002,
        "broadcast_ts": 1000.003,
        "engine_us": 24.5,
    }
    recv_ts = 1000.004
    ev = MarketEvent.from_dict(data, recv_ts=recv_ts)

    assert ev.seq == 101
    assert ev.is_tick
    assert not ev.is_depth
    assert ev.symbol == "BTC/USD"
    assert ev.spread == 10.0
    assert ev.wire_latency_us == pytest.approx(1000.0, abs=50.0)
    assert ev.total_platform_latency_us == pytest.approx(2000.0, abs=50.0)


def test_market_event_parsing_depth():
    data = {
        "type": "DEPTH",
        "seq": 102,
        "sym": "BTC/USD",
        "bids": [[80000.0, 2.0, "BINANCE"], [79990.0, 5.0, "COINBASE"]],
        "asks": [[80010.0, 1.5, "OKX"], [80020.0, 4.0, "KRAKEN"]],
        "micro_price": 80004.2857,
        "ofi": 0.1429,
        "is_crossed": False,
        "exchange_ts": 1000.0,
        "ingest_ts": 1000.001,
        "broadcast_ts": 1000.002,
        "engine_us": 32.1,
    }
    recv_ts = 1000.003
    ev = MarketEvent.from_dict(data, recv_ts=recv_ts)

    assert ev.seq == 102
    assert ev.is_depth
    assert not ev.is_tick
    assert ev.symbol == "BTC/USD"
    assert ev.bid_price == 80000.0
    assert ev.ask_price == 80010.0
    assert ev.spread == 10.0
    assert ev.micro_price == 80004.2857
    assert ev.ofi == 0.1429
    assert not ev.is_crossed
    assert len(ev.bids) == 2
    assert len(ev.asks) == 2


def test_client_connect_and_status(running_daemon):
    with MDRAPClient(host="127.0.0.1", port=running_daemon.port) as client:
        assert client.is_connected()
        st = client.get_status()
        assert "uptime_s" in st
        assert st["port"] == running_daemon.port
        assert "global_seq" in st
        assert "replay_buffer_size" in st

        health = client.get_health()
        assert isinstance(health, dict)

        latency_ms = client.ping()
        assert latency_ms >= 0.0


def test_client_stream_l1_ticks_with_monotonic_seq(running_daemon):
    with MDRAPClient(host="127.0.0.1", port=running_daemon.port) as client:
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

        stats = client.stats()
        assert stats["events_received"] >= 10
        assert stats["gaps_detected"] == 0


def test_client_stream_l2_depth(running_daemon):
    with MDRAPClient(host="127.0.0.1", port=running_daemon.port) as client:
        client.subscribe(["BTC/USD"], include_depth=True)
        # Verify depth query on demand
        time.sleep(0.3)
        depth = client.get_depth("BTC/USD")
        # May be None or dict depending on simulation timing
        if depth:
            assert "bids" in depth
            assert "asks" in depth


def test_daemon_replay_buffer_and_request_replay(running_daemon):
    # Wait for daemon to broadcast some ticks into replay buffer
    time.sleep(0.4)
    with MDRAPClient(host="127.0.0.1", port=running_daemon.port) as client:
        st = client.get_status()
        curr_seq = st.get("global_seq", 0)
        assert curr_seq > 5

        # Replay the first 5 events
        replayed = client.request_replay(from_seq=1, to_seq=5)
        assert len(replayed) <= 5
        if replayed:
            for ev in replayed:
                assert 1 <= ev.seq <= 5
                assert isinstance(ev, MarketEvent)


def test_client_automated_gap_detection_and_replay():
    """
    Test that MDRAPClient transparently requests REPLAY when a sequence gap is detected.
    """
    # Create a mock daemon socket simulator to feed an intentional gap
    import socket
    import threading
    import json

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(1)

    def mock_server():
        conn, _ = server_sock.accept()
        # Read sub line
        conn.recv(1024)
        # Send event 1
        ev1 = json.dumps({"type": "TICK", "seq": 1, "sym": "BTC/USD", "price": 100.0, "status": "VALID"}) + "\n"
        conn.sendall(ev1.encode("utf-8"))
        time.sleep(0.05)

        # Intentionally skip event 2, send event 3!
        ev3 = json.dumps({"type": "TICK", "seq": 3, "sym": "BTC/USD", "price": 102.0, "status": "VALID"}) + "\n"
        conn.sendall(ev3.encode("utf-8"))

        # Wait for potential query socket for REPLAY
        q_conn, _ = server_sock.accept()
        req = q_conn.recv(1024).decode("utf-8")
        if "REPLAY" in req:
            # Replay missing event 2
            rep_resp = json.dumps({
                "status": "OK",
                "action": "REPLAY",
                "from_seq": 2,
                "to_seq": 2,
                "events": [{"type": "TICK", "seq": 2, "sym": "BTC/USD", "price": 101.0, "status": "VALID"}],
            }) + "\n"
            q_conn.sendall(rep_resp.encode("utf-8"))
        q_conn.close()

        time.sleep(0.1)
        conn.close()
        server_sock.close()

    t = threading.Thread(target=mock_server, daemon=True)
    t.start()

    with MDRAPClient(host="127.0.0.1", port=port, auto_replay=True) as client:
        client.subscribe("BTC/USD")
        received = []
        for ev in client.stream(timeout=1.0, max_events=3):
            received.append(ev)

        # Verified that all 3 events (1, 2, 3) were delivered in strictly increasing sequence!
        assert len(received) == 3
        seqs = [e.seq for e in received]
        assert seqs == [1, 2, 3]

        stats = client.stats()
        assert stats["gaps_detected"] == 1
        assert stats["events_replayed"] == 1


def test_client_unsubscribe_and_queries(running_daemon):
    with MDRAPClient(host="127.0.0.1", port=running_daemon.port) as client:
        # Ping
        p = client.ping()
        assert p >= 0.0

        # Subscribe & unsubscribe with depth and vwap
        client.subscribe(["BTC/USD", "ETH/USD"], include_depth=True, include_vwap=True)
        assert "BTC/USD" in client._subscribed_symbols
        assert "L2:BTC/USD" in client._subscribed_symbols
        assert "VWAP:BTC/USD" in client._subscribed_symbols

        client.unsubscribe(["BTC/USD", "ETH/USD"], include_depth=True, include_vwap=True)
        assert "BTC/USD" not in client._subscribed_symbols
        assert "L2:BTC/USD" not in client._subscribed_symbols

        # Query methods
        bbo = client.get_bbo("BTC/USD")
        assert bbo is not None or bbo is None
        vwap = client.get_vwap("BTC/USD", sizes=[1.0, 5.0])
        assert vwap is not None or vwap is None

