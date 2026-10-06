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
    with pytest.raises(ValueError, match="require_auth=True requires a valid security_manager"):
        TCPGatewayServer(host="127.0.0.1", port=9000, security_manager=None, require_auth=True)


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
    with pytest.raises(RuntimeError, match="Failed to persist API key revocation to storage"):
        sec.revoke_api_key(ent.token)

    assert ent.is_active is True, "In-memory state must not be modified if storage write fails"


def test_6d_require_api_key_salt_in_strict_modes():
    # When require_env_secrets is True, missing MDRAP_API_KEY_SALT must raise ValueError
    old_salt = os.environ.pop("MDRAP_API_KEY_SALT", None)
    old_sec = os.environ.get("MDRAP_SECRET_FEEDX")
    try:
        os.environ["MDRAP_SECRET_FEEDX"] = "secret123"
        with pytest.raises(ValueError, match="MDRAP_API_KEY_SALT is not set in environment"):
            SecurityManager(require_env_secrets=True)
    finally:
        if old_salt:
            os.environ["MDRAP_API_KEY_SALT"] = old_salt
        if old_sec:
            os.environ["MDRAP_SECRET_FEEDX"] = old_sec
        else:
            os.environ.pop("MDRAP_SECRET_FEEDX", None)
