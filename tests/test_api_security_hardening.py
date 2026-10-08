"""Phase 12 API Security Hardening & Attack Resistance Tests.

Tests:
1. Authentication: Bearer token in Authorization header vs X-API-Key
2. Rejection of query-string ?token= on REST endpoints (prevents log leakage)
3. Health check separation:
   - /liveness (returns 200 alive)
   - /readiness (checks DB connectivity)
   - /health (full diagnostic payload)
   - /v1/liveness, /v1/readiness, /v1/health parity
4. Authenticated /metrics endpoint when MDRAP_METRICS_AUTH=1
5. API Key revocation and scope enforcement
6. CORS configuration with explicit origins
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from mdrap.api import AppState, create_app, _resolve_client_ip
from mdrap.security import SecurityManager, Role
from mdrap.storage import Store


@pytest.fixture
def api_client(tmp_path):
    db_file = str(tmp_path / "sec_api.db")
    store = Store(db_file)
    sec = SecurityManager(store=store)

    viewer = sec.register_api_key(client_id="ViewerClient", role=Role.VIEWER)
    operator = sec.register_api_key(client_id="OperatorClient", role=Role.OPERATOR)
    admin = sec.register_api_key(client_id="AdminClient", role=Role.ADMIN)
    store.commit()

    state = AppState(db_path=db_file, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    yield {
        "client": client,
        "state": state,
        "store": store,
        "sec": sec,
        "viewer_token": viewer.token,
        "operator_token": operator.token,
        "admin_token": admin.token,
    }
    store.close()


def test_bearer_token_authentication(api_client):
    """Endpoints accept 'Authorization: Bearer <token>' and reject missing or bad tokens."""
    client = api_client["client"]
    token = api_client["viewer_token"]

    # 1. No token -> 401
    res_no_auth = client.get("/v1/feeds")
    assert res_no_auth.status_code == 401

    # 2. Invalid Bearer token -> 401
    res_bad = client.get("/v1/feeds", headers={"Authorization": "Bearer bad_token_123"})
    assert res_bad.status_code in (401, 403)

    # 3. Valid Bearer token -> 200
    res_valid = client.get("/v1/feeds", headers={"Authorization": f"Bearer {token}"})
    assert res_valid.status_code == 200


def test_invalid_key_burst_does_not_lock_out_a_valid_key(api_client):
    """Lockout state is scoped to invalid key prefixes and never preempts valid auth."""
    client = api_client["client"]
    for _ in range(12):
        response = client.get(
            "/v1/feeds", headers={"X-API-Key": "attacker-key-prefix-invalid"}
        )
        assert response.status_code in (401, 429)

    valid = client.get("/v1/feeds", headers={"X-API-Key": api_client["viewer_token"]})
    assert valid.status_code == 200


def test_forwarded_client_address_requires_trusted_proxy(monkeypatch):
    from types import SimpleNamespace

    request = SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.10"),
        headers={"X-Forwarded-For": "203.0.113.8, 198.51.100.20"},
    )
    monkeypatch.delenv("MDRAP_TRUSTED_PROXY_IPS", raising=False)
    assert _resolve_client_ip(request) == "198.51.100.10"

    monkeypatch.setenv("MDRAP_TRUSTED_PROXY_IPS", "198.51.100.10")
    assert _resolve_client_ip(request) == "203.0.113.8"
    request.headers["X-Forwarded-For"] = "not-an-ip"
    assert _resolve_client_ip(request) == "198.51.100.10"


def test_query_param_token_rejected_on_rest(api_client):
    """Passing token as query parameter on REST is rejected to prevent log leakage."""
    client = api_client["client"]
    token = api_client["viewer_token"]

    res = client.get(f"/v1/feeds?token={token}")
    assert res.status_code in (401, 403)


def test_health_liveness_readiness_separation(api_client):
    """Verify separate /liveness, /readiness, and /health endpoints."""
    client = api_client["client"]

    # Root probes
    res_live = client.get("/liveness")
    assert res_live.status_code == 200
    assert res_live.json()["status"] == "alive"

    res_ready = client.get("/readiness")
    assert res_ready.status_code == 200
    assert res_ready.json()["status"] == "ready"

    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"

    # /v1/ probes
    assert client.get("/v1/liveness").status_code == 200
    assert client.get("/v1/readiness").status_code == 200
    assert client.get("/v1/health").status_code == 200


def test_readiness_fails_when_pipeline_writer_is_degraded(api_client):
    from mdrap.pipeline import WriterFailure

    api_client["state"].pipeline._writer_failure = WriterFailure("double fault")
    response = api_client["client"].get("/readiness")
    assert response.status_code == 503
    assert "writer" in response.json()["detail"]["reason"].lower()


def test_api_key_revocation_enforced(api_client):
    """Revoking an active API key immediately revokes access."""
    client = api_client["client"]
    sec = api_client["sec"]
    token = api_client["operator_token"]

    # Verify initial access
    res = client.get("/v1/feeds", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

    # Revoke key
    sec.revoke_api_key(token)

    # Immediately blocked
    res_revoked = client.get("/v1/feeds", headers={"Authorization": f"Bearer {token}"})
    assert res_revoked.status_code in (401, 403)


def test_ambiguous_key_prefix_revocation_returns_conflict(api_client):
    sec = api_client["sec"]
    first = sec.register_api_key("prefix-one", role=Role.OPERATOR)
    second = sec.register_api_key("prefix-two", role=Role.OPERATOR)
    first.key_prefix = second.key_prefix = "ambiguous-prefix"

    response = api_client["client"].delete(
        "/v1/keys/ambiguous-prefix",
        headers={"X-API-Key": api_client["admin_token"]},
    )
    assert response.status_code == 409
    assert first.is_active and second.is_active
