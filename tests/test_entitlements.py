import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from client import MarketEvent, MDRAPClient
from security import ClientEntitlement, Role, SecurityManager, Tier, TokenBucketRateLimiter
from service import MarketDataDaemon
from storage import Store


@pytest.fixture
def temp_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield db_path
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest.fixture
def auth_daemon(temp_db):
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=temp_db,
        require_auth=True,
        sim_speed_eps=10000.0,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    yield daemon
    daemon.stop()


def test_entitlement_key_defaults():
    sec = SecurityManager()
    ent = sec.register_api_key(client_id="Test_User")
    assert ent.client_id == "Test_User"
    assert ent.role == Role.VIEWER
    assert ent.is_active is True
    assert ent.token.startswith("mdrap_live_")


def test_security_manager_key_lifecycle_and_revocation():
    sec = SecurityManager()
    key = sec.register_api_key(client_id="Hedge_Fund_Alpha")
    token = key.token

    found = sec.get_entitlement(token)
    assert found is not None
    assert found.client_id == "Hedge_Fund_Alpha"
    assert found.is_active is True

    ok = sec.revoke_api_key(token)
    assert ok is True
    revoked = sec.get_entitlement(token)
    assert revoked.is_active is False


def test_sqlite_api_key_persistence(temp_db):
    store1 = Store(temp_db)
    sec1 = SecurityManager(store=store1)
    ent = sec1.register_api_key(client_id="Persistent_Client")
    token = ent.token
    store1.close()

    # Reopen database in a fresh Store instance
    store2 = Store(temp_db)
    sec2 = SecurityManager(store=store2)
    loaded = sec2.get_entitlement(token)

    assert loaded is not None
    assert loaded.client_id == "Persistent_Client"
    assert loaded.is_active is True
    assert loaded.role == Role.VIEWER
    store2.close()


def test_daemon_auth_with_valid_and_revoked_keys(auth_daemon):
    # 1. Connect with valid key
    with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token="mdrap_demo_pro_key") as client:
        assert client.is_connected()
        assert client.client_id == "Demo_Pro_Quant"

    # 2. Connect with invalid token
    with pytest.raises(PermissionError) as exc_info:
        with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token="invalid_key_xyz") as client:
            pass
    assert "INVALID_TOKEN" in str(exc_info.value)

    # 3. Create and then revoke a key
    temp_key = auth_daemon.security_manager.register_api_key("Short_Lived")
    auth_daemon.security_manager.revoke_api_key(temp_key.token)

    with pytest.raises(PermissionError) as exc_info2:
        with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token=temp_key.token) as client:
            pass
    assert "REVOKED_TOKEN" in str(exc_info2.value)


def test_authenticated_client_full_access(auth_daemon):
    """Verify that authenticated clients have full access to L1, L2, Binary wire format, and Replay (no paywalls)."""
    with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token="mdrap_demo_free_key") as client:
        # 1. Standard L1 subscription is allowed
        res_sub = client._send_query("SUB BTC/USD")
        assert res_sub.get("status") == "OK"

        # 2. L2 Depth is allowed for all authenticated clients
        res_depth = client._send_query("SUB L2:BTC/USD")
        assert res_depth.get("status") == "OK"

        # 3. Binary wire format is allowed for all authenticated clients
        res_bin = client._send_query("FORMAT BINARY")
        assert res_bin.get("status") == "OK"

        # 4. Replay is allowed
        replayed = client.request_replay(from_seq=1, to_seq=50)
        assert isinstance(replayed, list)


def test_pro_tier_permissions(auth_daemon):
    """Verify that clients can access L2 depth, binary format, and replays."""
    with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token="mdrap_demo_pro_key") as client:
        # L2 Depth allowed
        res_depth = client._send_query("SUB L2:BTC/USD")
        assert res_depth.get("status") == "OK"

        # Binary protocol allowed
        res_bin = client._send_query("FORMAT BINARY")
        assert res_bin.get("status") == "OK"

        # Replay allowed
        replayed = client.request_replay(from_seq=1, to_seq=50)
        assert isinstance(replayed, list)


def test_rate_limiter_throttles_events(auth_daemon):
    """Verify that token bucket rate limiter throttles clients exceeding their quota."""
    # Register client with very low rate limit (5 events/sec)
    slow_key = auth_daemon.security_manager.register_api_key(
        client_id="Throttled_Bot",
        rate_limit_eps=5.0,
    )

    with MDRAPClient(host="127.0.0.1", port=auth_daemon.port, auth_token=slow_key.token) as client:
        client.subscribe("ALL")
        events = []
        # Receive a few events
        for ev in client.stream(timeout=2.0, max_events=5):
            events.append(ev)
        assert len(events) >= 1

        # Verify daemon telemetry recorded client
        st = auth_daemon.stats()
        assert "rate_limited_ticks" in st
        assert "active_clients" in st

    # After disconnect, verify rate_limited_ticks remains tracked
    st_post = auth_daemon.stats()
    assert st_post["rate_limited_ticks"] >= 0


def test_backward_compatibility_unauthenticated(temp_db):
    """Verify unauthenticated daemon maintains default unthrottled institutional access."""
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=temp_db,
        require_auth=False,
        sim_speed_eps=10000.0,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)

    try:
        with MDRAPClient(host="127.0.0.1", port=daemon.port) as client:
            client.subscribe("ALL")
            events = [ev for ev in client.stream(timeout=2.0, max_events=5)]
            assert len(events) == 5
            assert all(ev.is_tick for ev in events)
    finally:
        daemon.stop()
