"""
Tests for MDRAP Commercial REST Endpoints.

Verifies:
- All 14 REST endpoints operate as specified.
- Config endpoint never leaks pre-shared secrets or tokens.
- Audits and standalone cryptographic proofs export and verify accurately.
- BBO and depth ladders return structured responses.
"""

from __future__ import annotations

import os
import tempfile
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient

from api import AppState, create_app
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from security import SecurityManager
from storage import Store


@pytest.fixture
def api_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(db_path)
    sec = SecurityManager(store=store)

    adm_ent = sec.register_api_key(client_id="MasterAdmin", role="ADMIN")
    op_ent = sec.register_api_key(client_id="MasterOp", role="OPERATOR")
    view_ent = sec.register_api_key(client_id="MasterViewer", role="VIEWER")
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
        "admin_key": adm_ent.token,
        "operator_key": op_ent.token,
        "viewer_key": view_ent.token,
        "db_path": db_path,
    }

    store.close()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_health_endpoint(api_env):
    client = api_env["client"]
    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert data["version"] == "2.2.0"
    assert "uptime_seconds" in data
    assert "db" in data
    assert "watchdog" in data


def test_feeds_crud(api_env):
    client = api_env["client"]
    adm_headers = {"X-API-Key": api_env["admin_key"]}
    view_headers = {"X-API-Key": api_env["viewer_key"]}

    # List feeds initially
    r_list = client.get("/v1/feeds", headers=view_headers)
    assert r_list.status_code == 200
    assert len(r_list.json()) >= 1

    # Register new feed (Admin)
    new_feed = {
        "source": "POLYGON_EQUITIES",
        "provider": "polygon",
        "symbols": ["AAPL", "MSFT", "GOOGL"],
        "secret": "test_feed_secret_123",
    }
    r_create = client.post("/v1/feeds", json=new_feed, headers=adm_headers)
    assert r_create.status_code == 200
    assert r_create.json()["source"] == "POLYGON_EQUITIES"
    assert r_create.json()["status"] == "ok"

    # List feeds again
    r_list2 = client.get("/v1/feeds", headers=view_headers)
    sources = [f["source"] for f in r_list2.json()]
    assert "POLYGON_EQUITIES" in sources

    # Delete feed (Admin)
    r_del = client.delete("/v1/feeds/POLYGON_EQUITIES", headers=adm_headers)
    assert r_del.status_code == 200
    assert r_del.json()["status"] == "ok"


def test_events_and_quality(api_env):
    client = api_env["client"]
    state = api_env["state"]
    store = api_env["store"]
    op_headers = {"X-API-Key": api_env["operator_key"]}
    view_headers = {"X-API-Key": api_env["viewer_key"]}

    # Seed events
    ev = CanonicalEvent(
        event_id="1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        processing_timestamp=1700000000.002,
        source="FEEDX",
        sequence_number=1,
        price=150.25,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )
    store.write_canonical_batch([ev])
    store.commit()

    # Query events
    r_ev = client.get("/v1/events?instrument_id=AAPL", headers=view_headers)
    assert r_ev.status_code == 200
    events = r_ev.json()
    assert len(events) >= 1
    assert events[0]["instrument_id"] == "AAPL"

    # Query quality summary
    r_q = client.get("/v1/quality", headers=view_headers)
    assert r_q.status_code == 200
    q_data = r_q.json()
    assert "feed_reliability_scores" in q_data
    assert "security" in q_data

    # Query quarantine (requires Operator)
    r_quar = client.get("/v1/quarantine", headers=op_headers)
    assert r_quar.status_code == 200
    assert isinstance(r_quar.json(), list)


def test_audit_endpoints(api_env):
    client = api_env["client"]
    op_headers = {"X-API-Key": api_env["operator_key"]}

    # Log an action
    state = api_env["state"]
    state.store.append_audit(
        actor="TestUser",
        role="OPERATOR",
        action="CONFIG_UPDATE",
        details="threshold updated to 0.05",
    )

    # 1. Get audit log
    r_log = client.get("/v1/audit", headers=op_headers)
    assert r_log.status_code == 200
    logs = r_log.json()
    assert len(logs) >= 1
    assert logs[0]["action"] == "CONFIG_UPDATE"

    # 2. Verify audit chain
    r_ver = client.get("/v1/audit/verify", headers=op_headers)
    assert r_ver.status_code == 200
    assert r_ver.json()["verified"] is True
    assert r_ver.json()["entries_checked"] >= 1

    # 3. Export standalone proof bundle
    r_exp = client.get("/v1/audit/export", headers=op_headers)
    assert r_exp.status_code == 200
    bundle = r_exp.json()
    assert bundle["integrity_verified"] is True
    assert "entries" in bundle
    assert bundle["total_entries"] >= 1


def test_bbo_and_depth_endpoints(api_env):
    import time

    client = api_env["client"]
    state = api_env["state"]
    view_headers = {"X-API-Key": api_env["viewer_key"]}

    now = time.time()
    # Update BBO in engine
    quote_ev = CanonicalEvent(
        event_id="Q1",
        instrument_id="BTC/USD",
        event_type=EventType.QUOTE,
        exchange_timestamp=now,
        receive_timestamp=now,
        processing_timestamp=now,
        source="BINANCE",
        sequence_number=1,
        bid_price=50000.0,
        bid_size=1.5,
        ask_price=50001.0,
        ask_size=2.0,
        quality_status=QualityStatus.VALID,
    )
    state.watchdog.observe(quote_ev)
    state.bbo.observe(quote_ev)

    # Query BBO
    r_bbo = client.get("/v1/bbo/BTC%2FUSD", headers=view_headers)
    assert r_bbo.status_code == 200
    res = r_bbo.json()
    bbo_data = res.get("bbo", res)
    assert bbo_data["best_bid"] == 50000.0
    assert bbo_data["best_ask"] == 50001.0

    # Query Depth
    r_depth = client.get("/v1/depth/BTC%2FUSD", headers=view_headers)
    assert r_depth.status_code == 200
    depth_data = r_depth.json()["depth"]
    assert "bids" in depth_data
    assert "asks" in depth_data


def test_config_endpoint_redacts_secrets(api_env):
    client = api_env["client"]
    adm_headers = {"X-API-Key": api_env["admin_key"]}

    r_cfg = client.get("/v1/config", headers=adm_headers)
    assert r_cfg.status_code == 200
    cfg = r_cfg.json()

    # Verify no raw secrets or tokens exist in config response
    cfg_str = str(cfg)
    assert api_env["admin_key"] not in cfg_str
    assert "mdrap_live_" not in cfg_str
    assert (
        "secret" not in cfg_str.lower()
        or cfg["security"]["cryptographic_feed_secrets_sealed"] is True
    )


def test_keys_crud_lifecycle(api_env):
    client = api_env["client"]
    adm_headers = {"X-API-Key": api_env["admin_key"]}

    # 1. Create Key
    create_payload = {
        "client_id": "EnterpriseClient_XYZ",
        "role": "OPERATOR",
    }
    r_create = client.post("/v1/keys", json=create_payload, headers=adm_headers)
    assert r_create.status_code == 200
    new_key_data = r_create.json()
    raw_token = new_key_data["token"]
    pfx = new_key_data["key_prefix"]
    assert raw_token.startswith("mdrap_live_")
    assert new_key_data["role"] == "OPERATOR"

    # 2. List Keys
    r_list = client.get("/v1/keys", headers=adm_headers)
    assert r_list.status_code == 200
    prefixes = [k["key_prefix"] for k in r_list.json()]
    assert pfx in prefixes
    # Verify raw token is never returned in list
    assert raw_token not in [k.get("token") for k in r_list.json() if "token" in k]

    # 3. Authenticate with newly created key
    r_auth = client.get("/v1/events", headers={"X-API-Key": raw_token})
    assert r_auth.status_code == 200

    # 4. Revoke Key by prefix
    r_rev = client.delete(f"/v1/keys/{pfx}", headers=adm_headers)
    assert r_rev.status_code == 200
    assert r_rev.json()["status"] == "ok"

    # 5. Verify revoked key can no longer authenticate
    r_auth_rev = client.get("/v1/events", headers={"X-API-Key": raw_token})
    assert r_auth_rev.status_code == 401
