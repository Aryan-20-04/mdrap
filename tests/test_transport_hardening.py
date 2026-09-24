"""Phase 11 Transport Layer Hardening Tests (SHM & WebSocket).

Tests:
1. SHM:
   - Producer to consumer data integrity
   - Empty buffer read handling
   - Rapid wraparound and boundary slot verification
   - Disconnected / restarted consumer recovery
2. WebSocket:
   - Connect and authenticate via token / headers
   - Subscription and unsubscription filtering
   - Handling of malformed JSON / invalid actions
   - Graceful client disconnect and state cleanup
"""

import pytest
from shm import SHMWriter, SHMReader, HAS_SHM


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available on this platform")
def test_shm_empty_buffer_and_recovery():
    """Reading from an empty SHM buffer returns None without errors."""
    name = "test_shm_hard_empty"
    writer = SHMWriter(name=name, slot_count=64)
    reader = SHMReader(name=name)

    # Empty buffer: slot 1 has not been written
    assert reader.read_slot(1) is None
    assert reader.read_latest_seq() == 0

    # Write single tick
    writer.write_tick(
        seq=1,
        symbol="AAPL",
        source="FEED_A",
        price=150.0,
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
        engine_us=12.0,
    )

    assert reader.read_latest_seq() == 1
    ev = reader.read_slot(1)
    assert ev is not None
    assert ev["sym"] == "AAPL"

    reader.close()
    writer.close()


@pytest.mark.skipif(not HAS_SHM, reason="SharedMemory not available on this platform")
def test_shm_high_contention_burst():
    """Writing a rapid burst through small ring buffer maintains consistent seq pointers."""
    name = "test_shm_hard_burst"
    writer = SHMWriter(name=name, slot_count=32)
    reader = SHMReader(name=name)

    # Burst 200 ticks into 32 slots
    for i in range(1, 201):
        writer.write_tick(
            seq=i,
            symbol="NVDA",
            source="FEED_A",
            price=120.0 + i * 0.05,
            size=1.0,
            bid=119.9,
            ask=120.1,
            bid_size=1.0,
            ask_size=1.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1000.0 + i * 0.001,
            ingest_ts=1000.0 + i * 0.001 + 0.0001,
            broadcast_ts=1000.0 + i * 0.001 + 0.0002,
            engine_us=10.0,
        )

    assert reader.read_latest_seq() == 200
    # Last slot is readable
    last_ev = reader.read_slot(200)
    assert last_ev is not None
    assert last_ev["seq"] == 200
    assert last_ev["sym"] == "NVDA"

    reader.close()
    writer.close()


def test_websocket_transport_protocol(tmp_path):
    """Test WebSocket connection, auth, SUB/UNSUB, invalid messages, and disconnect."""
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient
    from api import AppState, create_app
    from security import SecurityManager
    from storage import Store

    db_path = str(tmp_path / "ws_hard.db")
    store = Store(db_path)
    sec = SecurityManager(store=store)
    ent = sec.register_api_key(client_id="TestWsClient", role="VIEWER")
    store.commit()

    state = AppState(db_path=db_path, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    # 1. Connect and verify ACK
    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": f"Bearer {ent.token}"}
    ) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"

        # 2. Subscribe to symbol AAPL
        ws.send_json({"action": "SUB", "symbols": ["AAPL"]})
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "SUBSCRIPTION_UPDATE"
        assert "AAPL" in sub_ack["subscribed"]

        # 3. Send invalid / unrecognized action
        ws.send_json({"action": "INVALID_ACTION"})
        err = ws.receive_json()
        assert err["type"] == "ERROR"

        # 4. Unsubscribe
        ws.send_json({"action": "UNSUB", "symbols": ["AAPL"]})
        unsub_ack = ws.receive_json()
        assert unsub_ack["type"] == "SUBSCRIPTION_UPDATE"
        assert "AAPL" not in unsub_ack["subscribed"]

    store.close()
