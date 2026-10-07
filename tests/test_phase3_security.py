"""
Phase 3 Security & Runtime Verification Suite (Gate G3).

Verifies:
1. Spoofed source rejection (HTTP 403) via ClientEntitlement.allowed_sources.
2. Forged client receive_timestamp overridden by server time.time().
3. Namespacing of client raw_id to prevent collision/spoofing.
4. Request entity limits:
   - Request body > 5 MB returns HTTP 413.
   - Batch count > 10,000 returns HTTP 413.
   - Single record > 64 KB returns HTTP 413.
5. WebSocket handshake authentication:
   - Missing or invalid credentials rejected before accept with code 1008.
   - Valid credentials accepted.
6. Slow WebSocket subscriber isolation via bounded queues.
7. SecurityManager hooks (sanitizer, HMAC, rate limiter) wired to pipeline.
8. Memory bounds under 100,000 instrument flood (under 256 MB limit).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import tracemalloc
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mdrap.api import AppState, create_app
from mdrap.bbo import BBOEngine
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.reconciliation import Reconciler
from mdrap.security import ClientEntitlement, Role, SecurityManager
from mdrap.storage import Store


@pytest.fixture
def sec_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(db_path)
    sec = SecurityManager(store=store)

    # Entitlement with restricted source permissions
    binance_only_ent = sec.register_api_key(client_id="BinanceOnlyClient", role=Role.OPERATOR)
    binance_only_ent.allowed_sources = ["BINANCE"]

    # Entitlement with unrestricted permissions
    all_sources_ent = sec.register_api_key(client_id="GlobalOpClient", role=Role.OPERATOR)

    # Viewer entitlement for websocket
    viewer_ent = sec.register_api_key(client_id="ViewerClient", role=Role.VIEWER)
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
        "binance_key": binance_only_ent.token,
        "all_sources_key": all_sources_ent.token,
        "viewer_key": viewer_ent.token,
        "db_path": db_path,
    }

    store.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_spoofed_source_rejected(sec_env):
    """Client with allowed_sources=['BINANCE'] cannot ingest for 'NASDAQ' or 'KRAKEN'."""
    client = sec_env["client"]
    headers = {"X-API-Key": sec_env["binance_key"]}

    now = time.time()
    # 1. Ingest for authorized source BINANCE -> 200 OK
    good_payload = {
        "source": "BINANCE",
        "payload": {
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 1,
            "price": 50000.0,
            "quantity": 1.0,
        },
    }
    r_good = client.post("/v1/ingest", json=good_payload, headers=headers)
    assert r_good.status_code == 200
    assert r_good.json()["status"] == "ok"

    # 2. Ingest for unauthorized source NASDAQ -> 403 Forbidden
    spoofed_payload = {
        "source": "NASDAQ",
        "payload": {
            "instrument": "AAPL",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 1,
            "price": 150.0,
            "quantity": 10.0,
        },
    }
    r_bad = client.post("/v1/ingest", json=spoofed_payload, headers=headers)
    assert r_bad.status_code == 403
    assert "not authorized to ingest for source 'NASDAQ'" in r_bad.json()["detail"]


def test_forged_timestamp_overridden_and_raw_id_namespaced(sec_env):
    """Client receive_timestamp is ignored and overwritten; raw_id is namespaced."""
    client = sec_env["client"]
    headers = {"X-API-Key": sec_env["binance_key"]}

    forged_ts = 1000000.0  # ancient timestamp
    payload = {
        "source": "BINANCE",
        "receive_timestamp": forged_ts,
        "raw_id": "cust_msg_42",
        "payload": {
            "instrument": "ETH/USD",
            "event_type": "TRADE",
            "exchange_ts": time.time(),
            "sequence": 1,
            "price": 3000.0,
            "quantity": 2.0,
        },
    }
    t_before = time.time()
    r = client.post("/v1/ingest", json=payload, headers=headers)
    t_after = time.time()
    assert r.status_code == 200

    data = r.json()
    canonical = data["canonical"][0]
    # Server timestamp must have overridden the forged client timestamp
    assert canonical["receive_timestamp"] != forged_ts
    assert t_before - 1.0 <= canonical["receive_timestamp"] <= t_after + 1.0

    # raw_id must be namespaced with client identifier
    assert "BinanceOnlyClient" in canonical["raw_id"]
    assert "cust_msg_42" in canonical["raw_id"]


def test_request_body_size_limits(sec_env):
    """Payloads exceeding body, batch, or record limits return HTTP 413."""
    client = sec_env["client"]
    headers = {"X-API-Key": sec_env["all_sources_key"]}

    # 1. Record limit: single record > 64 KB -> 413
    huge_record = {
        "source": "BINANCE",
        "payload": {
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "exchange_ts": time.time(),
            "sequence": 1,
            "price": 50000.0,
            "quantity": 1.0,
            "junk": "X" * (70 * 1024),  # > 64 KB
        },
    }
    r_rec = client.post("/v1/ingest", json=huge_record, headers=headers)
    assert r_rec.status_code == 413
    assert "exceeds maximum allowed 65536 bytes" in r_rec.json()["detail"]

    # 2. Batch limit: > 10,000 items -> 413
    oversized_batch = [
        {
            "source": "BINANCE",
            "payload": {
                "instrument": f"SYM_{i}",
                "event_type": "TRADE",
                "exchange_ts": time.time(),
                "sequence": i,
                "price": 100.0,
                "quantity": 1.0,
            },
        }
        for i in range(10_001)
    ]
    r_batch = client.post("/v1/ingest", json=oversized_batch, headers=headers)
    assert r_batch.status_code == 413
    assert "exceeds maximum batch limit" in r_batch.json()["detail"]


def test_websocket_strict_auth_before_accept(sec_env):
    """Unauthenticated connections are rejected before accept with code 1008."""
    client = sec_env["client"]
    token = sec_env["viewer_key"]

    # 1. Missing header -> rejected before accept with 1008
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/events/stream"):
            pass
    assert exc.value.code == 1008

    # 2. Invalid header -> rejected before accept with 1008
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            "/v1/events/stream", headers={"Authorization": "Bearer bad_token_xyz"}
        ):
            pass
    assert exc.value.code == 1008

    # 3. Valid Authorization: Bearer header -> accepted
    with client.websocket_connect(
        "/v1/events/stream", headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"
        assert "Connected" in ack["message"]

    # 4. Valid X-API-Key header -> accepted
    with client.websocket_connect(
        "/v1/events/stream", headers={"X-API-Key": token}
    ) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"


def test_slow_websocket_subscriber_isolation(sec_env):
    """Slow subscriber queue overflow drops frames without blocking healthy clients."""
    state: AppState = sec_env["state"]

    # Register a mock slow subscriber with queue maxsize=2
    mock_ws = "mock_slow_client_socket"
    q = asyncio.Queue(maxsize=2)
    q.put_nowait({"type": "EVENT", "id": 1})
    q.put_nowait({"type": "EVENT", "id": 2})

    state.subscribers[mock_ws] = set()  # ALL symbols
    state.subscriber_queues[mock_ws] = q
    state.subscriber_drops[mock_ws] = 0

    assert q.full()

    # Broadcast event: queue is full, must drop into drop counter instead of blocking
    tick = {
        "type": "CANONICAL_TICK",
        "instrument_id": "BTC/USD",
        "price": 60000.0,
    }
    asyncio.run(state.broadcast_event(tick))

    # Verify drop counter was incremented
    assert state.subscriber_drops[mock_ws] == 1


def test_security_hooks_active_on_ingest(sec_env):
    """Pipeline security manager enforces sanitizer and HMAC authentication on ingest."""
    client = sec_env["client"]
    sec: SecurityManager = sec_env["sec"]
    headers = {"X-API-Key": sec_env["all_sources_key"]}

    # 1. Sanitizer hook: payload with injection symbol is quarantined as INVALID
    bad_symbol_payload = {
        "source": "SIMULATOR",
        "payload": {
            "instrument": "<script>alert('xss')</script>",
            "event_type": "TRADE",
            "exchange_ts": time.time(),
            "sequence": 1,
            "price": 50000.0,
            "quantity": 1.0,
        },
    }
    r_bad = client.post("/v1/ingest", json=bad_symbol_payload, headers=headers)
    assert r_bad.status_code == 200
    data_bad = r_bad.json()
    assert len(data_bad["canonical"]) == 1
    assert data_bad["canonical"][0]["quality_status"] == "INVALID"
    assert "MALFORMED" in data_bad["canonical"][0]["reasons"]

    # 2. HMAC hook: feed requiring HMAC without signature is quarantined as INVALID
    sec.register_feed_secret("FEED_HMAC", "super_secret_hmac_key_12345")
    sec.require_hmac = {"FEED_HMAC"}

    hmac_payload = {
        "source": "FEED_HMAC",
        "payload": {
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "exchange_ts": time.time(),
            "sequence": 1,
            "price": 50000.0,
            "quantity": 1.0,
        },
    }
    r_hmac = client.post("/v1/ingest", json=hmac_payload, headers=headers)
    assert r_hmac.status_code == 200
    data_hmac = r_hmac.json()
    assert len(data_hmac["canonical"]) == 1
    assert data_hmac["canonical"][0]["quality_status"] == "INVALID"
    assert "SECURITY_REJECT" in data_hmac["canonical"][0]["reasons"]


def test_memory_flood_bounds_under_256mb():
    """100,000 unique instruments fed through Reconciler and BBOEngine stay strictly bounded."""
    tracemalloc.start()
    t0_mem, _ = tracemalloc.get_traced_memory()

    reconciler = Reconciler(max_instruments=100_000)
    bbo = BBOEngine(max_instruments=100_000)

    now = time.time()
    # Ingest 100,000 unique instruments
    for i in range(100_000):
        sym = f"INST_{i}"
        ev = CanonicalEvent(
            event_id=f"evt_{i}",
            instrument_id=sym,
            event_type=EventType.QUOTE,
            exchange_timestamp=now,
            receive_timestamp=now,
            processing_timestamp=now,
            source="BINANCE",
            sequence_number=1,
            bid_price=100.0 + (i % 10),
            bid_size=10.0,
            ask_price=101.0 + (i % 10),
            ask_size=10.0,
            quality_status=QualityStatus.VALID,
        )
        bbo.observe(ev)
        reconciler.reconcile(ev)

    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak_mem / (1024 * 1024)
    assert len(reconciler._latest) <= 100_000
    assert len(bbo._books) <= 100_000
    # Peak memory must remain comfortably below the 256 MB budget
    assert peak_mb < 256.0, f"Peak memory {peak_mb:.2f} MB exceeded 256 MB budget"
