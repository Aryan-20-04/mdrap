"""Phase 1 Regression Suite: Persistent-Engine Initialization & Failure Modes (Spec §5 & Task 1).

Verifies that:
1. Production mode with storage/WAL initialization failure NEVER silently degrades to in-memory engine.
2. /ready returns HTTP 503 Service Unavailable when storage initialization fails in production.
3. /v1/ingest rejects payloads with HTTP 503 Service Unavailable when durable engine is unavailable.
4. /health exposes effective persistence_mode ("production_durable" vs "development_in_memory").
5. Development mode works cleanly when explicitly configured via :memory: or MDRAP_DEV_MODE=1.
"""

import os
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from mdrap.api import AppState, create_app
from mdrap.security import SecurityManager, Role
from mdrap.storage import Store, PersistenceMode


def test_production_mode_storage_failure_blocks_readiness_and_ingest(tmp_path):
    """Storage initialization failure in production mode sets init_error, returns 503, and refuses writes."""
    db_file = tmp_path / "prod.db"
    wal_dir = tmp_path / "prod.wal"
    store = Store(str(db_file))
    sec = SecurityManager(store=store)
    op_key = sec.register_api_key(client_id="ProdTester", role=Role.OPERATOR)

    # Simulate uncreatable WAL directory or open failure by raising during Engine.open
    with patch("mdrap.engine.Engine.open", side_effect=OSError("Disk mount read-only / permission denied")):
        state = AppState(
            db_path=str(db_file),
            store=store,
            security_manager=sec,
            wal_path=str(wal_dir),
        )

    # Invariants:
    assert state.persistence_mode == PersistenceMode.PRODUCTION_DURABLE.value
    assert state.init_error is not None
    assert "Disk mount read-only" in state.init_error
    assert state.engine is None  # Never silently fell back to in-memory Engine!

    app = create_app(state=state)
    client = TestClient(app)

    # 1. Readiness check MUST return 503
    r_ready = client.get("/ready")
    assert r_ready.status_code == 503
    assert "Durable storage initialization failed" in r_ready.json()["detail"]["reason"]

    # 2. Health check reports persistence_mode and unhealthy
    r_health = client.get("/health")
    assert r_health.status_code == 200
    health_data = r_health.json()
    assert health_data["status"] == "unhealthy"
    assert health_data["persistence_mode"] == PersistenceMode.PRODUCTION_DURABLE.value

    # 3. Ingestion MUST fail with 503 and never write to in-memory fallback
    r_ingest = client.post(
        "/v1/ingest",
        json={"source": "FEEDX", "payload": {"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 10.0}},
        headers={"X-API-Key": op_key.token},
    )
    assert r_ingest.status_code == 503
    assert "Durable storage unavailable" in r_ingest.json()["detail"]

    store.close()


def test_development_mode_explicit_in_memory_allowed(monkeypatch):
    """Development mode is allowed when explicitly configured via MDRAP_DEV_MODE=1 or :memory:."""
    monkeypatch.setenv("MDRAP_DEV_MODE", "1")
    store = Store(":memory:")
    sec = SecurityManager(store=store)
    op_key = sec.register_api_key(client_id="DevTester", role=Role.OPERATOR)

    state = AppState(db_path=":memory:", store=store, security_manager=sec)

    assert state.persistence_mode == PersistenceMode.DEVELOPMENT_IN_MEMORY.value
    assert state.init_error is None
    assert state.engine is not None

    app = create_app(state=state)
    client = TestClient(app)

    r_ready = client.get("/ready")
    assert r_ready.status_code == 200
    assert r_ready.json()["status"] == "ready"

    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["persistence_mode"] == PersistenceMode.DEVELOPMENT_IN_MEMORY.value

    r_ingest = client.post(
        "/v1/ingest",
        json={"source": "FEEDX", "payload": {"instrument": "AAPL", "event_type": "TRADE", "price": 150.0, "quantity": 10.0}},
        headers={"X-API-Key": op_key.token},
    )
    assert r_ingest.status_code == 200
    assert r_ingest.json()["ingested"] == 1

    store.close()
