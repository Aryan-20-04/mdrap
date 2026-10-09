"""
Phase 2 Observability & Health Invariants Tests (Workstream D).

Verifies:
1. /liveness returns HTTP 200 when process is running.
2. /readiness returns HTTP 200 when engine and database are operational.
3. /readiness returns HTTP 503 with exact reason when WAL is poisoned or storage fails.
4. /health separates healthy, degraded, and unhealthy states truthfully based on actual component telemetry.
"""

from unittest.mock import MagicMock
import pytest
from starlette.testclient import TestClient

from mdrap.api import AppState, create_app


def test_liveness_endpoint():
    """Verify /liveness returns HTTP 200 and alive status."""
    state = AppState(db_path=":memory:")
    app = create_app(state=state)
    with TestClient(app) as client:
        r = client.get("/v1/liveness")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "alive"
        assert data["uptime_seconds"] >= 0.0


def test_readiness_healthy_and_failure():
    """Verify /readiness returns 200 when ready, and 503 when storage/engine fails."""
    state = AppState(db_path=":memory:")
    app = create_app(state=state)
    with TestClient(app) as client:
        # 1. Happy path readiness
        r = client.get("/v1/readiness")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"

        # 2. Simulated poisoned WAL IngestLog
        mock_log = MagicMock()
        mock_log._is_poisoned = True
        state.engine.log = mock_log

        r_fail = client.get("/v1/readiness")
        assert r_fail.status_code == 503
        fail_data = r_fail.json()
        assert "Engine IngestLog WAL is poisoned" in fail_data["detail"]["reason"]


def test_health_truthful_states():
    """Verify /health reports healthy, degraded, and unhealthy truthfully."""
    state = AppState(db_path=":memory:")
    app = create_app(state=state)
    with TestClient(app) as client:
        # 1. Normal state -> healthy
        r1 = client.get("/v1/health")
        assert r1.status_code == 200
        assert r1.json()["status"] == "healthy"
        assert r1.json()["degraded"] is False

        # 2. Simulated degraded state (subscriber drops)
        state.total_subscriber_drops = 1500
        r2 = client.get("/v1/health")
        assert r2.status_code == 200
        assert r2.json()["status"] == "degraded"
        assert r2.json()["degraded"] is True
        assert "Excessive WebSocket subscriber drops" in r2.json()["degraded_reason"]

        # 3. Simulated fatal init error -> unhealthy
        state.init_error = "Durable storage initialization failed: disk full"
        r3 = client.get("/v1/health")
        assert r3.status_code == 200
        assert r3.json()["status"] == "unhealthy"
