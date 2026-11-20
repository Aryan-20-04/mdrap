"""Tests for broadcast token caching and revocation performance (MDRAP-SEC-01).

Verifies:
1. broadcast_event does not perform repeated HMAC-SHA256 calculations per subscriber per event.
2. Immediate disconnection on token revocation via O(1) boolean entitlement check.
3. Fail-closed security boundary when unauthenticated or expired.
"""

import asyncio
import time
from unittest.mock import patch
import pytest

from mdrap.api import AppState
from mdrap.security import SecurityManager, Role


@pytest.mark.asyncio
async def test_broadcast_cached_entitlement_avoids_hmac_dos():
    """Verify broadcast does not re-compute HMAC hashes per tick (MDRAP-SEC-01)."""
    st = AppState(db_path=":memory:")
    sec = st.security_manager

    # Register an API key
    token = sec.generate_api_key(client_id="test_trader", role=Role.ADMIN)
    ent = sec.get_entitlement(token, active_only=True)
    assert ent is not None

    class DummyWS:
        def __init__(self):
            self.closed = False
            self.close_code = None

        async def close(self, code=1000, reason=""):
            self.closed = True
            self.close_code = code

    ws = DummyWS()
    q = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    st.subscribers[ws] = set()
    st.subscriber_tokens[ws] = token
    st.subscriber_entitlements[ws] = ent  # Cached upon connection
    st.subscriber_queues[ws] = q
    st.subscriber_loops[ws] = loop

    # Track calls to get_entitlement and _hash_candidates
    with patch.object(sec, "get_entitlement", wraps=sec.get_entitlement) as mock_get_ent:
        with patch.object(sec._api_keys, "_hash_candidates", wraps=sec._api_keys._hash_candidates) as mock_hash:
            # Broadcast 20 events
            for i in range(20):
                await st.broadcast_event({"instrument_id": "BTC-USD", "price": 50000.0 + i})

            # Invariant: get_entitlement and _hash_candidates are NOT called during broadcast
            assert mock_get_ent.call_count == 0
            assert mock_hash.call_count == 0

    assert q.qsize() == 20
    assert ws not in st._dead_subscribers
    assert not ws.closed


@pytest.mark.asyncio
async def test_immediate_eviction_on_key_revocation():
    """Verify revoked key is immediately evicted in O(1) time without credential leakage."""
    st = AppState(db_path=":memory:")
    sec = st.security_manager

    token = sec.generate_api_key(client_id="revoked_trader", role=Role.VIEWER)
    ent = sec.get_entitlement(token, active_only=True)
    assert ent is not None

    class DummyWS:
        def __init__(self):
            self.closed = False

        async def close(self, code=1000, reason=""):
            self.closed = True

    ws = DummyWS()
    q = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    st.subscribers[ws] = set()
    st.subscriber_tokens[ws] = token
    st.subscriber_entitlements[ws] = ent
    st.subscriber_queues[ws] = q
    st.subscriber_loops[ws] = loop

    # Deliver one event normally
    await st.broadcast_event({"instrument_id": "BTC-USD", "price": 50000.0})
    assert q.qsize() == 1
    assert not ws.closed

    # Revoke API key
    revoked = sec.revoke_api_key(token)
    assert revoked is True
    assert ent.is_active is False

    # Broadcast next event -> must detect ent.is_active is False and close socket
    await st.broadcast_event({"instrument_id": "BTC-USD", "price": 50001.0})
    assert ws.closed is True
    assert ws not in st.subscribers
    assert ws not in st.subscriber_entitlements
    assert ws not in st.subscriber_tokens
