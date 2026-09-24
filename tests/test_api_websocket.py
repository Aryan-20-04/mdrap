"""
Tests for Real-Time WebSocket Event Streaming Distribution.

Verifies:
- Authentication via query param or initial handshake message.
- Rejection of unauthenticated WebSocket connections with error & code 1008.
- Subscription protocol (SUB, UNSUB, PING/PONG).
- Real-time event broadcasting to filtered subscribers.
"""

from __future__ import annotations

import os
import tempfile
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import AppState, create_app
from security import SecurityManager
from storage import Store


@pytest.fixture
def ws_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(db_path)
    sec = SecurityManager(store=store)

    view_ent = sec.register_api_key(client_id="StreamingViewer", role="VIEWER")
    store.commit()

    state = AppState(db_path=db_path, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    yield {
        "client": client,
        "app": app,
        "state": state,
        "store": store,
        "sec": sec,
        "token": view_ent.token,
        "db_path": db_path,
    }

    store.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_websocket_auth_via_headers_and_first_frame(ws_env):
    client = ws_env["client"]
    token = ws_env["token"]

    # 1. Handshake with Authorization: Bearer header
    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"
        assert "Connected" in ack["message"]

        # Send PING
        ws.send_json({"action": "PING"})
        resp = ws.receive_json()
        assert resp["type"] == "PONG"
        assert "timestamp" in resp

    # 2. Handshake with X-API-Key header
    with client.websocket_connect(
        "/v1/events/stream", headers={"X-API-Key": token}
    ) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"

    # 3. First-frame JSON authentication
    with client.websocket_connect("/v1/events/stream") as ws:
        ws.send_json({"action": "authenticate", "token": token})
        ack = ws.receive_json()
        assert ack["type"] == "ACK"
        assert "Connected" in ack["message"]


def test_websocket_rejects_unauthenticated(ws_env):
    client = ws_env["client"]
    token = ws_env["token"]

    # Query param ?token= is rejected
    with client.websocket_connect(f"/v1/events/stream?token={token}") as ws:
        err = ws.receive_json()
        assert err["type"] == "ERROR"
        assert "Query-parameter ?token= is not supported" in err["error"]
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008

    # Invalid Bearer header is rejected
    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": "Bearer invalid_token_123"}
    ) as ws:
        err = ws.receive_json()
        assert err["type"] == "ERROR"
        assert "Unauthorized" in err["error"]
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


def test_websocket_subscription_and_unsubscription(ws_env):
    client = ws_env["client"]
    token = ws_env["token"]

    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        # Consume initial ACK
        ack = ws.receive_json()
        assert ack["type"] == "ACK"

        # Subscribe to AAPL and BTC/USD
        ws.send_json({"action": "SUB", "symbols": ["AAPL", "BTC/USD"]})
        resp1 = ws.receive_json()
        assert resp1["type"] == "SUBSCRIPTION_UPDATE"
        assert "AAPL" in resp1["subscribed"]
        assert "BTC/USD" in resp1["subscribed"]

        # Unsubscribe from AAPL
        ws.send_json({"action": "UNSUB", "symbols": ["AAPL"]})
        resp2 = ws.receive_json()
        assert resp2["type"] == "SUBSCRIPTION_UPDATE"
        assert "AAPL" not in resp2["subscribed"]
        assert "BTC/USD" in resp2["subscribed"]


def test_websocket_realtime_broadcast(ws_env):
    import asyncio
    client = ws_env["client"]
    token = ws_env["token"]
    state = ws_env["state"]

    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        # Consume initial ACK
        ack = ws.receive_json()
        assert ack["type"] == "ACK"

        # Subscribe to BTC/USD
        ws.send_json({"action": "SUB", "symbols": ["BTC/USD"]})
        sub_resp = ws.receive_json()
        assert sub_resp["type"] == "SUBSCRIPTION_UPDATE"

        # Broadcast event from engine
        event_payload = {
            "type": "CANONICAL_TICK",
            "instrument_id": "BTC/USD",
            "price": 65432.10,
            "quantity": 0.75,
            "source": "BINANCE",
        }
        # Run broadcast on state
        asyncio.run(state.broadcast_event(event_payload))

        # Client receives event
        received = ws.receive_json()
        assert received["type"] == "CANONICAL_TICK"
        assert received["instrument_id"] == "BTC/USD"
        assert received["price"] == 65432.10
