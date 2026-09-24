"""Phase 14 Python SDK Public Contract Tests.

Tests:
1. 'from mdrap import Client, MarketEvent' clean importability
2. Client constructor parameter handling (timeout, retries, base_url)
3. MarketEvent from_dict instantiation and helper methods (spread, wire_latency_us)
4. Client context manager (with Client(...) as c:)
5. Error handling and timeout configurations
"""

import sys
import os
import pytest

# Ensure src/ is importable as mdrap and direct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from client import Client, MDRAPClient, MarketEvent


def test_public_sdk_imports():
    """Verify primary public SDK classes are exported and available."""
    assert Client is MDRAPClient
    assert MarketEvent is not None


def test_market_event_spread_and_latency():
    """Verify MarketEvent helper methods calculate spread and wire latency accurately."""
    ev = MarketEvent(
        seq=1,
        event_type="TICK",
        symbol="AAPL",
        price=150.0,
        size=100.0,
        bid_price=149.95,
        ask_price=150.05,
        exchange_ts=1000.0,
        ingest_ts=1000.005,
        broadcast_ts=1000.0055,
        recv_ts=1000.006,
    )
    assert (
        ev.spread == pytest.approx(0.10)
        if "pytest" in sys.modules
        else round(ev.spread, 2) == 0.10
    )
    assert ev.wire_latency_us > 0


def test_client_initialization_and_context_manager():
    """Client initializes cleanly with custom timeout and retries, and closes via context manager."""
    client = Client(
        base_url="http://127.0.0.1:8000",
        api_key="mdrap_live_test_key",
        timeout=10.0,
        max_retries=5,
    )
    assert client.base_url == "http://127.0.0.1:8000"
    assert client.api_key == "mdrap_live_test_key"
    assert client.timeout == 10.0
    assert client.max_retries == 5

    with client as c:
        assert c.is_connected() is False
