"""Tests for Security Hardening Review Fixes.

Covers:
1. /metrics authorization bypass prevention via spoofed X-Forwarded-For headers.
2. Trusted proxy evaluation via MDRAP_TRUSTED_PROXY_IPS.
3. Denial of query string ?token= on REST endpoints to avoid access log leakage.
4. Retention of query string token support on WebSocket handshake.
5. Strict alignment of key_prefix exposure to 12 characters (docs/security.md spec).
"""

from __future__ import annotations

import os
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from api import create_app
from config import PlatformConfig
from security import Role, SecurityManager
from storage import Store


class RemotePeerMiddleware:
    """ASGI middleware to simulate arbitrary remote TCP peer addresses."""

    def __init__(self, asgi_app, peer_host: str = "198.51.100.1", peer_port: int = 54321):
        self.app = asgi_app
        self.peer_host = peer_host
        self.peer_port = peer_port

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["client"] = (self.peer_host, self.peer_port)
        await self.app(scope, receive, send)


@pytest.fixture
def auth_app_and_keys(monkeypatch, tmp_path):
    """Fixture initializing an app instance with metrics authentication required."""
    monkeypatch.setenv("MDRAP_METRICS_AUTH", "1")
    monkeypatch.delenv("MDRAP_TRUSTED_PROXY_IPS", raising=False)

    db_path = str(tmp_path / "sec_test.db")
    app = create_app(db_path=db_path)
    store = app.state.mdrap.store

    sec_mgr: SecurityManager = app.state.mdrap.security_manager
    admin_ent = sec_mgr.register_api_key(client_id="SecAdmin", role=Role.ADMIN)
    viewer_ent = sec_mgr.register_api_key(client_id="SecViewer", role=Role.VIEWER)

    yield app, admin_ent, viewer_ent, store

    try:
        store.close()
    except Exception:
        pass


def test_metrics_spoofed_forwarded_for_rejected(auth_app_and_keys):
    """An external client cannot bypass /metrics auth by spoofing X-Forwarded-For."""
    app, admin_ent, viewer_ent, store = auth_app_and_keys

    # Simulate direct remote connection from untrusted IP
    remote_app = RemotePeerMiddleware(app, peer_host="198.51.100.1")
    client = TestClient(remote_app)

    # 1. Unauthenticated request without headers -> 401
    r_unauth = client.get("/metrics")
    assert r_unauth.status_code == 401

    # 2. Spoofed X-Forwarded-For asserting 127.0.0.1 -> MUST STILL BE 401
    r_spoofed_ip = client.get("/metrics", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r_spoofed_ip.status_code == 401
    assert "requires authentication" in r_spoofed_ip.text

    # 3. Spoofed X-Forwarded-For asserting localhost -> MUST STILL BE 401
    r_spoofed_host = client.get("/metrics", headers={"X-Forwarded-For": "localhost"})
    assert r_spoofed_host.status_code == 401

    # 4. Valid API key supplied via X-API-Key -> 200 OK
    r_authed = client.get("/metrics", headers={"X-API-Key": viewer_ent.token})
    assert r_authed.status_code == 200
    assert "mdrap_events_processed_total" in r_authed.text

    # 5. Valid API key supplied via Bearer Authorization -> 200 OK
    r_bearer = client.get("/metrics", headers={"Authorization": f"Bearer {viewer_ent.token}"})
    assert r_bearer.status_code == 200


def test_metrics_trusted_proxy_evaluation(auth_app_and_keys, monkeypatch):
    """X-Forwarded-For is trusted ONLY when the TCP peer is listed in MDRAP_TRUSTED_PROXY_IPS."""
    app, admin_ent, viewer_ent, store = auth_app_and_keys
    proxy_ip = "10.0.0.2"
    monkeypatch.setenv("MDRAP_TRUSTED_PROXY_IPS", f"10.0.0.1, {proxy_ip}, 10.0.0.3")

    # Client connects through trusted reverse proxy (peer_host=proxy_ip)
    proxied_app = RemotePeerMiddleware(app, peer_host=proxy_ip)
    client = TestClient(proxied_app)

    # Case A: Proxy forwards a loopback client -> Allowed without token
    r_loopback = client.get("/metrics", headers={"X-Forwarded-For": "127.0.0.1"})
    assert r_loopback.status_code == 200

    # Case B: Proxy forwards a remote client (e.g. 203.0.113.50) without token -> 401
    r_remote = client.get("/metrics", headers={"X-Forwarded-For": "203.0.113.50"})
    assert r_remote.status_code == 401

    # Case C: Proxy forwards a remote client with valid token -> 200
    r_remote_authed = client.get(
        "/metrics",
        headers={"X-Forwarded-For": "203.0.113.50", "X-API-Key": viewer_ent.token},
    )
    assert r_remote_authed.status_code == 200


def test_rest_endpoints_reject_query_string_token(auth_app_and_keys):
    """REST endpoints must reject ?token= to prevent credential leakage in logs."""
    app, admin_ent, viewer_ent, store = auth_app_and_keys
    client = TestClient(app)

    # 1. GET /v1/events with ?token= -> 401 Unauthorized
    r_query = client.get(f"/v1/events?token={viewer_ent.token}")
    assert r_query.status_code == 401
    assert "Missing API key in X-API-Key or Authorization header" in r_query.text

    # 2. GET /v1/events with X-API-Key header -> 200 OK
    r_header = client.get("/v1/events", headers={"X-API-Key": viewer_ent.token})
    assert r_header.status_code == 200

    # 3. GET /v1/events with Authorization: Bearer -> 200 OK
    r_bearer = client.get("/v1/events", headers={"Authorization": f"Bearer {viewer_ent.token}"})
    assert r_bearer.status_code == 200

    # 4. POST /v1/keys with ?token= -> 401 Unauthorized
    r_post_query = client.post(
        f"/v1/keys?token={admin_ent.token}",
        json={"client_id": "LeakTest", "role": "VIEWER"},
    )
    assert r_post_query.status_code == 401


def test_websocket_accepts_query_token(auth_app_and_keys):
    """WebSocket handshake continues to support ?token= where custom headers are unsupported."""
    app, admin_ent, viewer_ent, store = auth_app_and_keys
    client = TestClient(app)

    # Valid token in query string -> Handshake accepted
    with client.websocket_connect(f"/v1/events/stream?token={viewer_ent.token}") as ws:
        init_frame = ws.receive_json()
        assert init_frame["type"] in ("ACK", "SUBSCRIPTION_STATUS")

    from starlette.websockets import WebSocketDisconnect

    # Invalid token in query string -> Receives error frame and disconnects with 1008
    with client.websocket_connect("/v1/events/stream?token=invalid_secret_token") as ws:
        err = ws.receive_json()
        assert err["type"] == "ERROR"
        assert "Unauthorized" in err["error"]
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1008


def test_key_prefix_length_aligned_to_documented_spec(tmp_path):
    """Verify key_prefix exposes only 12 characters as specified in docs/security.md."""
    db_path = str(tmp_path / "prefix_test.db")
    store = Store(db_path)
    sec = SecurityManager(store=store)

    ent = sec.register_api_key(client_id="PrefixClient", role="ADMIN")
    raw_token = ent.token
    assert raw_token.startswith("mdrap_live_")

    # Docs spec: token[:12] + "..."
    expected_prefix = raw_token[:12] + "..."
    assert ent.key_prefix == expected_prefix
    assert len(ent.key_prefix) == 15

    # Check that only 1 character beyond 'mdrap_live_' is exposed (11 fixed chars + 1 char)
    assert ent.key_prefix.startswith("mdrap_live_")
    assert len(ent.key_prefix.replace("mdrap_live_", "").replace("...", "")) == 1

    # Check persistence and retrieval
    store.commit()
    retrieved = store.load_api_keys()
    match = [k for k in retrieved if k.client_id == "PrefixClient"][0]
    assert match.key_prefix == expected_prefix

    store.close()
