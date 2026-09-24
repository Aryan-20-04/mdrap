"""
Tests for MDRAP Commercial Python SDK (MDRAPClient).

Verifies:
- Programmatic client configuration with base_url and api_key.
- Health, feeds, canonical queries, quality, and quarantine calls.
- Cryptographic audit trail verification and standalone proof export.
- Seamless fallback to REST for BBO and depth queries.
- Clean context manager support (with MDRAPClient(...) as client:).
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

import uvicorn

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from api import AppState, create_app
from client import MDRAPClient
from models import CanonicalEvent, EventType, QualityStatus
from security import SecurityManager
from storage import Store


@pytest.fixture(scope="module")
def live_api_server():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    store = Store(db_path)
    sec = SecurityManager(store=store)
    admin_ent = sec.register_api_key(client_id="SDK_Admin_User", role="ADMIN")
    store.commit()

    # Seed an event
    ev = CanonicalEvent(
        event_id="SDK_1",
        instrument_id="ETH/USD",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        processing_timestamp=1700000000.002,
        source="BINANCE",
        sequence_number=1,
        price=3500.50,
        quantity=2.0,
        quality_status=QualityStatus.VALID,
    )
    store.write_canonical_batch([ev])
    store.commit()

    state = AppState(db_path=db_path, store=store, security_manager=sec)
    app = create_app(state=state)

    def _get_free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    port = _get_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(0.5)

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key": admin_ent.token,
        "db_path": db_path,
        "server": server,
    }

    server.should_exit = True
    t.join(timeout=2.0)
    store.close()
    import gc
    gc.collect()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


def test_sdk_health_and_feeds(live_api_server):
    base_url = live_api_server["base_url"]
    api_key = live_api_server["api_key"]

    with MDRAPClient(base_url=base_url, api_key=api_key) as client:
        # Health check
        h = client.health()
        assert h["status"] == "healthy"
        assert h["version"] == "2.2.0"
        assert "uptime_seconds" in h

        # List feeds
        feeds = client.list_feeds()
        assert isinstance(feeds, list)
        assert len(feeds) >= 1

        # Register new feed
        reg = client.register_feed(
            source="COINBASE_PRO",
            provider="crypto",
            symbols=["BTC/USD", "ETH/USD"],
        )
        assert reg["status"] == "ok"
        assert reg["source"] == "COINBASE_PRO"

        # List again
        feeds_after = client.list_feeds()
        sources = [f["source"] for f in feeds_after]
        assert "COINBASE_PRO" in sources


def test_sdk_events_and_quality(live_api_server):
    base_url = live_api_server["base_url"]
    api_key = live_api_server["api_key"]

    with MDRAPClient(base_url=base_url, api_key=api_key) as client:
        # Query events
        events = client.query_events(instrument_id="ETH/USD")
        assert len(events) >= 1
        assert events[0]["instrument_id"] == "ETH/USD"
        assert events[0]["price"] == 3500.50

        # Query quality
        q = client.query_quality()
        assert "feed_reliability_scores" in q
        assert "security" in q

        # Query quarantine
        quar = client.query_quarantine()
        assert isinstance(quar, list)


def test_sdk_audit_verification_and_export(live_api_server):
    base_url = live_api_server["base_url"]
    api_key = live_api_server["api_key"]

    with MDRAPClient(base_url=base_url, api_key=api_key) as client:
        # Verify audit
        audit_res = client.verify_audit()
        assert audit_res["verified"] is True
        assert "entries_checked" in audit_res

        # Export audit proof
        proof = client.export_audit()
        assert proof["integrity_verified"] is True
        assert "entries" in proof
        assert proof["total_entries"] >= 1


def test_sdk_depth_rest_fallback(live_api_server):
    base_url = live_api_server["base_url"]
    api_key = live_api_server["api_key"]

    with MDRAPClient(base_url=base_url, api_key=api_key) as client:
        depth = client.get_depth("ETH/USD")
        assert isinstance(depth, dict)
        assert "bids" in depth
        assert "asks" in depth
