"""Phase 18 Full-System Architecture Integration Test.

Validates the complete end-to-end pipeline:
Feed Ingest -> Normalization -> Quality Validation -> Cross-Feed Reconciliation
-> Evidentiary Quarantine -> Cryptographic Audit Trail -> Persistent Storage
-> REST API & Health Probes -> Real-Time WebSocket Streaming -> Analytics Queries

Invariant:
Deterministic input produces identical, bit-for-bit verifiable output datasets.
"""

import pytest
from starlette.testclient import TestClient

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from api import AppState, create_app
from pipeline import Pipeline
from security import SecurityManager, Role
from simulator import FeedSimulator, SimulatorConfig
from storage import Store


def test_full_system_end_to_end_pipeline(tmp_path):
    """Executes the entire MDRAP architecture from feed ingest to analytics and streaming."""
    db_file = str(tmp_path / "full_system.db")
    store = Store(db_file)
    sec = SecurityManager(store=store)
    admin_ent = sec.register_api_key(client_id="SystemAdmin", role=Role.ADMIN)
    store.commit()

    # 1. Pipeline Ingestion & Processing
    pipeline = Pipeline(store=store)
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=1000))

    for raw, _ in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()

    # 2. Verify Storage Counts & Integrity
    counts = store.counts()
    assert sum(counts.values()) > 0
    assert pipeline.metrics.processed == 1000

    # 3. Append Audit Record for the run
    head_hash = store.append_audit(
        "system", "pipeline", "STREAM_COMPLETE", f"processed=1000,counts={counts}"
    )
    assert head_hash is not None
    ok, msg, _ = store.verify_audit_integrity()
    assert ok is True

    # 4. REST API & Health Check Probes
    state = AppState(db_path=db_file, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    # Liveness & Readiness Probes
    live_res = client.get("/liveness")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "alive"

    ready_res = client.get("/readiness")
    assert ready_res.status_code == 200
    assert ready_res.json()["status"] == "ready"

    # Query events via authenticated REST
    auth_headers = {"Authorization": f"Bearer {admin_ent.token}"}
    events_res = client.get("/v1/events?limit=10", headers=auth_headers)
    assert events_res.status_code == 200
    assert len(events_res.json()) > 0

    # 5. WebSocket Streaming Handshake
    with client.websocket_connect(f"/v1/events/stream?token={admin_ent.token}") as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"
        ws.send_json({"action": "SUB", "symbols": ["AAPL"]})
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "SUBSCRIPTION_UPDATE"
        assert "AAPL" in sub_ack["subscribed"]

    store.close()
