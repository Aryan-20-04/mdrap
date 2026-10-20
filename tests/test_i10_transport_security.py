"""Unit tests for Transport Security: Non-loopback binding TLS enforcement (Program I-10)."""

from __future__ import annotations

import os
import ssl
from unittest.mock import MagicMock
import pytest

from mdrap.service import MarketDataDaemon


def test_non_loopback_binding_rejected_without_tls() -> None:
    """Test non-loopback bindings (0.0.0.0, external IPs) are rejected without TLS."""
    # 1. 0.0.0.0 wildcard
    with pytest.raises(ValueError, match="Non-loopback binding '0.0.0.0' rejected"):
        MarketDataDaemon(host="0.0.0.0", enable_shm=False)

    # 2. External IPv4
    with pytest.raises(
        ValueError, match="Non-loopback binding '192.168.1.100' rejected"
    ):
        MarketDataDaemon(host="192.168.1.100", enable_shm=False)

    # 3. External IPv6
    with pytest.raises(ValueError, match="Non-loopback binding '2001:db8::1' rejected"):
        MarketDataDaemon(host="2001:db8::1", enable_shm=False)


def test_loopback_bindings_permitted_without_tls() -> None:
    """Test standard local loopback addresses do not require TLS."""
    for loopback in ("127.0.0.1", "localhost", "::1"):
        daemon = MarketDataDaemon(host=loopback, db_path=":memory:", enable_shm=False)
        assert daemon.host == loopback
        daemon.close()


def test_non_loopback_binding_allowed_with_ssl_context() -> None:
    """Test non-loopback binding succeeds when ssl_context is provided."""
    dummy_ctx = MagicMock(spec=ssl.SSLContext)
    daemon = MarketDataDaemon(
        host="0.0.0.0",
        ssl_context=dummy_ctx,
        db_path=":memory:",
        enable_shm=False,
    )
    assert daemon.host == "0.0.0.0"
    assert daemon.ssl_context is dummy_ctx
    daemon.close()


def test_non_loopback_binding_allowed_with_explicit_override() -> None:
    """Test non-loopback binding succeeds with allow_insecure_bind flag or env var."""
    # 1. Constructor flag
    daemon1 = MarketDataDaemon(
        host="0.0.0.0",
        allow_insecure_bind=True,
        db_path=":memory:",
        enable_shm=False,
    )
    assert daemon1.host == "0.0.0.0"
    daemon1.close()

    # 2. Environment variable override
    try:
        os.environ["MDRAP_ALLOW_INSECURE_BIND"] = "1"
        daemon2 = MarketDataDaemon(
            host="10.0.0.15",
            db_path=":memory:",
            enable_shm=False,
        )
        assert daemon2.host == "10.0.0.15"
        daemon2.close()
    finally:
        os.environ.pop("MDRAP_ALLOW_INSECURE_BIND", None)


def test_require_client_cert_requires_ca_cert() -> None:
    """Test that require_client_cert=True requires tls_ca_cert or ssl_context (Finding N-17)."""
    # 1. Constructor flag without CA cert raises ValueError
    with pytest.raises(
        ValueError, match="require_client_cert=True requires tls_ca_cert"
    ):
        MarketDataDaemon(
            host="127.0.0.1",
            require_client_cert=True,
            tls_ca_cert=None,
            db_path=":memory:",
            enable_shm=False,
        )

    # 2. Environment variable MDRAP_REQUIRE_CLIENT_CERT=1 without CA cert raises ValueError
    try:
        os.environ["MDRAP_REQUIRE_CLIENT_CERT"] = "1"
        with pytest.raises(
            ValueError, match="require_client_cert=True requires tls_ca_cert"
        ):
            MarketDataDaemon(
                host="127.0.0.1",
                tls_ca_cert=None,
                db_path=":memory:",
                enable_shm=False,
            )
    finally:
        os.environ.pop("MDRAP_REQUIRE_CLIENT_CERT", None)

    # 3. Supplying ssl_context directly bypasses CA cert check
    dummy_ctx = MagicMock(spec=ssl.SSLContext)
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        require_client_cert=True,
        ssl_context=dummy_ctx,
        db_path=":memory:",
        enable_shm=False,
    )
    assert daemon.require_client_cert is True
    daemon.close()
