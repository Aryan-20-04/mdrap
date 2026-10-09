"""
Phase 2 Backpressure & Bounded Resource Tests (Workstream B).

Verifies:
1. Client session queues in MarketDataDaemon are bounded and evict stalled clients on prolonged stall.
2. Global drop counters accurately track queue overflows across all consumer sessions.
3. Degradation status is triggered when drop thresholds are breached.
4. WebSocket subscriber queues in AppState are bounded, evict stalled clients, and report degraded state.
"""

import asyncio
import os
import queue
import socket
import threading
import time
import pytest
from unittest.mock import MagicMock

from mdrap.models import RawEvent, EventType
from mdrap.service import MarketDataDaemon, _ClientSession
from mdrap.api import AppState, create_app


def test_service_client_session_bounded_and_eviction():
    """Verify that a slow TCP consumer with a full queue gets evicted after max_dropped_ticks."""
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=":memory:",
        use_live=False,
        enable_shm=False,
    )
    
    # Mock client socket
    mock_sock = MagicMock(spec=socket.socket)
    sess = _ClientSession(
        sock=mock_sock,
        symbols={"ALL"},
        queue=queue.Queue(maxsize=5),
        max_dropped_ticks=10,
    )
    
    daemon._sessions[mock_sock] = sess
    daemon._subscribers[mock_sock] = sess.symbols

    raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 50000.0,
            "quantity": 1.0,
            "exchange_ts": time.time(),
            "sequence": 1,
        },
        receive_timestamp=time.time(),
    )

    # Fill queue to capacity (5 items)
    for _ in range(5):
        daemon._process_and_broadcast(raw)

    assert sess.queue.full()
    assert sess.dropped_ticks == 0
    assert sess.is_alive is True

    # Broadcast 9 more times -> drops accumulate to 9
    for _ in range(9):
        daemon._process_and_broadcast(raw)

    assert sess.dropped_ticks == 9
    assert sess.is_alive is True

    # 10th drop breaches max_dropped_ticks=10 -> evicted!
    daemon._process_and_broadcast(raw)
    assert sess.dropped_ticks == 10
    assert sess.is_alive is False


def test_service_degradation_state_on_excessive_drops():
    """Verify daemon is_degraded transitions to True when drop threshold is exceeded."""
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=":memory:",
        use_live=False,
        enable_shm=False,
    )
    
    degraded, reason = daemon.is_degraded()
    assert degraded is False
    assert reason == ""

    # Artificially simulate 5001 drops
    daemon._total_dropped = 5001
    degraded, reason = daemon.is_degraded()
    assert degraded is True
    assert "High client queue drop count" in reason

    stats = daemon.stats()
    assert stats["degraded"] is True
    assert "High client queue drop count" in stats["degraded_reason"]


@pytest.mark.asyncio
async def test_api_websocket_subscriber_bounded_and_eviction():
    """Verify WebSocket subscribers are bounded and evicted upon exceeding max drops."""
    app_state = AppState(db_path=":memory:")
    app_state.max_client_drops = 5

    # Create mock websocket and a small bounded queue
    mock_ws = MagicMock()
    app_state.subscribers[mock_ws] = {"ALL"}
    q = asyncio.Queue(maxsize=3)
    app_state.subscriber_queues[mock_ws] = q
    app_state.subscriber_loops[mock_ws] = asyncio.get_running_loop()

    # Fill queue to capacity
    for i in range(3):
        await app_state.broadcast_event({"instrument_id": "BTC/USD", "price": 50000 + i})

    assert q.full()
    assert app_state.total_subscriber_drops == 0

    # Next 4 broadcasts -> drops 1 to 4
    for i in range(4):
        await app_state.broadcast_event({"instrument_id": "BTC/USD", "price": 50010 + i})

    assert app_state.total_subscriber_drops == 4
    assert app_state.subscriber_drops[mock_ws] == 4
    assert mock_ws in app_state.subscribers

    # 5th drop reaches max_client_drops=5 -> evicted
    await app_state.broadcast_event({"instrument_id": "BTC/USD", "price": 50020})
    assert app_state.total_subscriber_drops == 5
    assert mock_ws not in app_state.subscribers


def test_api_degradation_state_and_health_response():
    """Verify AppState reports degraded when drops exceed 1000."""
    from starlette.testclient import TestClient

    app_state = AppState(db_path=":memory:")
    assert app_state.is_degraded()[0] is False

    app = create_app(state=app_state)
    client = TestClient(app)

    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert data["degraded"] is False

    # Simulate high drops
    app_state.total_subscriber_drops = 1005
    deg, reason = app_state.is_degraded()
    assert deg is True
    assert "Excessive WebSocket subscriber drops" in reason

    r2 = client.get("/v1/health")
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["status"] == "degraded"
    assert data2["degraded"] is True
    assert "Excessive WebSocket subscriber drops" in data2["degraded_reason"]
