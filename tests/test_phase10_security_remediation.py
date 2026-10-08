import os
import sys
import tempfile
import time
import pytest

from mdrap.security import SecurityManager, Role, TokenBucketRateLimiter, hash_api_key
from mdrap.storage import Store
from mdrap.alert_sinks import WebhookAlertSink, validate_webhook_url


def test_sec01_direct_hash_submission_auth_bypass_prevented():
    """Verify that submitting a raw SHA256/HMAC token_hash does NOT grant access (SEC-01)."""
    sec = SecurityManager()
    ent = sec.register_api_key(client_id="TestClient", role=Role.ADMIN)
    raw_token = ent.token
    token_hash = ent.token_hash

    assert raw_token is not None and token_hash is not None

    # Authenticating with actual bearer token succeeds
    auth_ent = sec.get_entitlement(raw_token, active_only=True)
    assert auth_ent is not None
    assert auth_ent.client_id == "TestClient"

    # Authenticating by submitting the leaked hash MUST FAIL (returns None)
    fake_ent = sec.get_entitlement(token_hash, active_only=True)
    assert fake_ent is None, (
        "Security bypass: Raw token hash was accepted as credential!"
    )

    with pytest.raises(Exception):
        sec.authorize(token_hash, "manage_keys")


def test_sec02_rate_limiter_eviction_reset_prevented():
    """Verify that flooding 1,025 source names does NOT evict depleted buckets to reset their quota (SEC-02)."""
    limiter = TokenBucketRateLimiter(rate=10.0, capacity=20.0)

    # Completely deplete "victim_feed"
    for _ in range(20):
        limiter.allow("victim_feed", 1.0)

    # 21st request must be denied
    assert limiter.allow("victim_feed", 1.0) is False

    # Now flood 1,100 other source names
    for i in range(1100):
        limiter.allow(f"flooder_{i}", 1.0)

    # "victim_feed" was throttled/depleted and must NOT have been evicted and reset to full 20 tokens!
    assert limiter.allow("victim_feed", 1.0) is False, (
        "Rate limit bypassed: Depleted bucket was evicted and reset to full capacity!"
    )


def test_sec03_database_api_key_revocation_salted_hash_match():
    """Verify Store.revoke_api_key successfully updates SQLite database when given the raw token (SEC-03)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)

        ent = sec.register_api_key(client_id="ClientRevoke", role=Role.OPERATOR)
        token = ent.token

        # Key is active initially
        active_keys = store.load_api_keys()
        assert any(k.client_id == "ClientRevoke" and k.is_active for k in active_keys)

        # Revoke by passing token to store directly
        success = store.revoke_api_key(token)
        assert success is True, "Store.revoke_api_key returned False!"

        # Re-check database rows
        refreshed_keys = store.load_api_keys()
        matching = [k for k in refreshed_keys if k.client_id == "ClientRevoke"]
        assert len(matching) == 1
        assert matching[0].is_active is False, (
            "Database record remained active due to hash algorithm mismatch!"
        )

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def test_sec04_ssrf_webhook_url_validation():
    """Verify WebhookAlertSink blocks loopback, private networks, and cloud metadata endpoints (SEC-04)."""
    # Cloud metadata endpoints must be blocked
    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="http://169.254.169.254/latest/meta-data/")

    # Loopback / localhost must be blocked
    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="http://127.0.0.1:8080/admin")

    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="http://localhost:5000/api")

    # Private RFC-1918 addresses must be blocked
    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="http://10.0.0.5/hook")

    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="http://192.168.1.1/hook")

    # Non-http schemes must be blocked
    with pytest.raises(ValueError, match="SSRF"):
        WebhookAlertSink(endpoint_url="file:///etc/passwd")
