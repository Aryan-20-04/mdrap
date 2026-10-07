import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from security import SecurityManager, Role
from storage import Store
from gateway_tcp import TCPGatewayServer


def test_6b_tcp_gateway_fail_closed_without_security_manager():
    # If require_auth is True but security_manager is None, server must refuse to construct
    with pytest.raises(
        ValueError, match="require_auth=True requires a valid security_manager"
    ):
        TCPGatewayServer(
            host="127.0.0.1", port=9000, security_manager=None, require_auth=True
        )


def test_6c_api_key_revocation_durable_before_memory():
    class FailingStore:
        def revoke_api_key(self, token_hash: str):
            raise IOError("Disk full: cannot commit key revocation")

    sec = SecurityManager(store=None)
    ent = sec.register_api_key("client_alpha", role=Role.OPERATOR)
    assert ent.is_active is True

    # Inject failing store
    sec.store = FailingStore()

    # Attempting revocation must raise RuntimeError and MUST NOT flip is_active to False in memory
    with pytest.raises(
        RuntimeError, match="Failed to persist API key revocation to storage"
    ):
        sec.revoke_api_key(ent.token)

    assert ent.is_active is True, (
        "In-memory state must not be modified if storage write fails"
    )


def test_6d_require_api_key_salt_in_strict_modes():
    # When require_env_secrets is True, missing MDRAP_API_KEY_SALT must raise ValueError
    old_salt = os.environ.pop("MDRAP_API_KEY_SALT", None)
    old_sec = os.environ.get("MDRAP_SECRET_FEEDX")
    try:
        os.environ["MDRAP_SECRET_FEEDX"] = "secret123"
        with pytest.raises(
            ValueError, match="MDRAP_API_KEY_SALT is not set in environment"
        ):
            SecurityManager(require_env_secrets=True)
    finally:
        if old_salt:
            os.environ["MDRAP_API_KEY_SALT"] = old_salt
        if old_sec:
            os.environ["MDRAP_SECRET_FEEDX"] = old_sec
        else:
            os.environ.pop("MDRAP_SECRET_FEEDX", None)


def test_api_key_salt_is_required_outside_demo(monkeypatch):
    monkeypatch.delenv("MDRAP_DEMO", raising=False)
    monkeypatch.delenv("MDRAP_REQUIRE_ENV_SECRETS", raising=False)
    monkeypatch.delenv("MDRAP_API_KEY_SALT", raising=False)
    with pytest.raises(ValueError, match="must be set.*outside demo mode"):
        SecurityManager()


def test_expired_key_is_not_deactivated_in_memory_before_persist(monkeypatch):
    class FailingStore:
        def revoke_api_key(self, token_hash: str):
            raise OSError("disk unavailable")

    sec = SecurityManager(store=None)
    ent = sec.register_api_key("client", role=Role.OPERATOR, expires_at=0)
    ent.is_rotating = True
    sec.store = FailingStore()
    with pytest.raises(RuntimeError, match="Failed to persist expired API key"):
        sec.cleanup_expired_rotated_keys()
    assert ent.is_active is True
    assert ent.is_rotating is True


def test_ambiguous_key_prefix_is_rejected(monkeypatch):
    monkeypatch.setenv("MDRAP_API_KEY_SALT", "phase1-test-salt")
    monkeypatch.delenv("MDRAP_DEMO", raising=False)
    sec = SecurityManager(store=None)
    first = sec.register_api_key("first", role=Role.OPERATOR, token="shared-token-one")
    second = sec.register_api_key(
        "second", role=Role.OPERATOR, token="shared-token-two"
    )
    first.key_prefix = second.key_prefix = "shared-prefix"

    with pytest.raises(ValueError, match="prefix is ambiguous"):
        sec.revoke_api_key("shared-prefix")
    assert first.is_active is True
    assert second.is_active is True


def test_rotation_persistence_failure_is_visible_and_rolls_back_memory(monkeypatch):
    monkeypatch.setenv("MDRAP_API_KEY_SALT", "phase1-test-salt")
    monkeypatch.delenv("MDRAP_DEMO", raising=False)

    class FailingStore:
        def save_api_key(self, entitlement):
            raise OSError("disk unavailable")

    sec = SecurityManager(store=None)
    old = sec.register_api_key("client", role=Role.OPERATOR)
    sec.store = FailingStore()
    with pytest.raises(RuntimeError, match="Failed to persist old API key rotation"):
        sec.rotate_api_key(old.token)
    assert old.is_active is True
    assert old.is_rotating is False
    assert old.expires_at is None


def test_tcp_gateway_require_tls_fails_without_ssl_context():
    # If require_tls is True but ssl_context is None, server must refuse to construct
    with pytest.raises(
        ValueError, match="require_tls=True requires a valid ssl_context"
    ):
        TCPGatewayServer(
            host="127.0.0.1", port=9000, ssl_context=None, require_tls=True
        )
