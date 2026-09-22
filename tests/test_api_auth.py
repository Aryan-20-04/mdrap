"""
Tests for API Authentication and Role-Based Access Control (RBAC).

Verifies:
- Missing, malformed, or invalid API keys return 401 Unauthorized.
- Revoked keys return 401 Unauthorized.
- Role enforcement:
    VIEWER cannot access OPERATOR or ADMIN endpoints (403 Forbidden).
    OPERATOR cannot access ADMIN endpoints (403 Forbidden).
    ADMIN can access all endpoints.
"""

from __future__ import annotations

import os
import tempfile
import pytest
from starlette.testclient import TestClient

from api import AppState, create_app
from security import SecurityManager
from storage import Store


@pytest.fixture
def auth_setup():
    """Create isolated test environment with VIEWER, OPERATOR, and ADMIN keys."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(db_path)
    sec = SecurityManager(store=store)

    viewer_ent = sec.register_api_key(client_id="TestViewer", role="VIEWER")
    operator_ent = sec.register_api_key(client_id="TestOperator", role="OPERATOR")
    admin_ent = sec.register_api_key(client_id="TestAdmin", role="ADMIN")
    store.commit()

    state = AppState(db_path=db_path, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    yield {
        "client": client,
        "sec": sec,
        "store": store,
        "viewer_key": viewer_ent.token,
        "operator_key": operator_ent.token,
        "admin_key": admin_ent.token,
        "db_path": db_path,
    }

    store.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_healthcheck_is_public_and_unauthenticated(auth_setup):
    """Healthcheck endpoint must be accessible without authentication for container monitors."""
    client = auth_setup["client"]
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_missing_api_key_returns_401(auth_setup):
    client = auth_setup["client"]
    r = client.get("/v1/events")
    assert r.status_code == 401
    assert "Missing API key" in r.json()["detail"]


def test_invalid_api_key_returns_401(auth_setup):
    client = auth_setup["client"]
    r = client.get("/v1/events", headers={"X-API-Key": "mdrap_invalid_bad_token_1234"})
    assert r.status_code == 401
    assert "Invalid or inactive API key" in r.json()["detail"]


def test_bearer_token_authorization_header(auth_setup):
    client = auth_setup["client"]
    v_key = auth_setup["viewer_key"]
    r = client.get("/v1/events", headers={"Authorization": f"Bearer {v_key}"})
    assert r.status_code == 200


def test_revoked_key_returns_401(auth_setup):
    client = auth_setup["client"]
    sec = auth_setup["sec"]
    v_key = auth_setup["viewer_key"]

    # Initial check passes
    r1 = client.get("/v1/events", headers={"X-API-Key": v_key})
    assert r1.status_code == 200

    # Revoke key
    sec.revoke_api_key(v_key)

    # Next check fails with 401
    r2 = client.get("/v1/events", headers={"X-API-Key": v_key})
    assert r2.status_code == 401


def test_viewer_access_boundaries(auth_setup):
    client = auth_setup["client"]
    v_key = auth_setup["viewer_key"]
    headers = {"X-API-Key": v_key}

    # VIEWER can access read-only endpoints
    assert client.get("/v1/health", headers=headers).status_code == 200
    assert client.get("/v1/events", headers=headers).status_code == 200
    assert client.get("/v1/quality", headers=headers).status_code == 200

    # VIEWER CANNOT access OPERATOR endpoints (e.g. quarantine, audit verification)
    r_quar = client.get("/v1/quarantine", headers=headers)
    assert r_quar.status_code == 403
    assert "OPERATOR" in r_quar.json()["detail"]

    r_audit = client.get("/v1/audit/verify", headers=headers)
    assert r_audit.status_code == 403
    assert "OPERATOR" in r_audit.json()["detail"]

    # VIEWER CANNOT access ADMIN endpoints (e.g. key management)
    r_keys = client.get("/v1/keys", headers=headers)
    assert r_keys.status_code == 403
    assert "ADMIN" in r_keys.json()["detail"]


def test_operator_access_boundaries(auth_setup):
    client = auth_setup["client"]
    op_key = auth_setup["operator_key"]
    headers = {"X-API-Key": op_key}

    # OPERATOR can access VIEWER and OPERATOR endpoints
    assert client.get("/v1/health", headers=headers).status_code == 200
    assert client.get("/v1/events", headers=headers).status_code == 200
    assert client.get("/v1/quarantine", headers=headers).status_code == 200
    assert client.get("/v1/audit/verify", headers=headers).status_code == 200

    # OPERATOR CANNOT access ADMIN endpoints
    r_keys = client.get("/v1/keys", headers=headers)
    assert r_keys.status_code == 403
    assert "ADMIN" in r_keys.json()["detail"]


def test_admin_access_all_endpoints(auth_setup):
    client = auth_setup["client"]
    adm_key = auth_setup["admin_key"]
    headers = {"X-API-Key": adm_key}

    assert client.get("/v1/health", headers=headers).status_code == 200
    assert client.get("/v1/events", headers=headers).status_code == 200
    assert client.get("/v1/quarantine", headers=headers).status_code == 200
    assert client.get("/v1/keys", headers=headers).status_code == 200
