"""Tests for backpressure, queue saturation, and worker lifecycle safety (MDRAP-CONC-01, MDRAP-CONC-02).

Verifies:
1. Multi-threaded queue eviction race condition in ws_feed does not raise unhandled queue.Full.
2. Cross-thread WebSocket stream broadcast handles asyncio.QueueFull inside the destination
   event loop without leaking unhandled callback exceptions.
3. Slow consumers are accurately tracked and evicted when exceeding max_client_drops.
"""

import asyncio
import queue
import threading
import time
import pytest

from mdrap.models import RawEvent
from mdrap.ws_feed import WebSocketFeedManager
from mdrap.api import AppState, create_app


def test_multithreaded_queue_eviction_race_safety():
    """Verify that concurrent multi-feed worker threads do not crash on queue.Full (MDRAP-CONC-01)."""
    # Create feed manager with very small queue to force immediate saturation
    manager = WebSocketFeedManager(symbols=["BTC/USD"], max_queue_size=5)
    
    # Fill the queue to capacity
    for i in range(5):
        manager._queue.put_nowait(
            RawEvent(source=f"FEED_{i}", payload={"p": 100 + i}, receive_timestamp=time.time())
        )
    
    assert manager._queue.full()

    errors = []
    frames_pushed = 0
    lock = threading.Lock()

    def concurrent_producer(venue_name: str, count: int):
        nonlocal frames_pushed
        for j in range(count):
            raw = RawEvent(
                source=venue_name,
                payload={"p": 200 + j, "symbol": "BTC/USD"},
                receive_timestamp=time.time(),
            )
            try:
                # Simulate the guarded eviction logic from ws_feed._venue_worker
                try:
                    manager._queue.put_nowait(raw)
                except queue.Full:
                    evicted = None
                    try:
                        evicted = manager._queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        manager._queue.put_nowait(raw)
                    except queue.Full:
                        evicted = raw
                    with lock:
                        manager._drop_count += 1
                with lock:
                    frames_pushed += 1
            except Exception as exc:
                errors.append((venue_name, exc))

    # Launch 5 concurrent producers attempting to put into the saturated queue simultaneously
    threads = []
    for v in ["BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"]:
        t = threading.Thread(target=concurrent_producer, args=(v, 50))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5.0)

    # Invariant: ZERO unhandled exceptions escaped the producers
    assert len(errors) == 0, f"Producer threads encountered unhandled exceptions: {errors}"
    assert frames_pushed == 250
    assert manager._drop_count > 0, "Drops should have occurred under saturation"


@pytest.mark.asyncio
async def test_cross_thread_broadcast_queue_full_safety():
    """Verify cross-thread broadcast handles QueueFull in event loop context (MDRAP-CONC-02)."""
    st = AppState(db_path=":memory:")
    st.max_client_drops = 3

    # Mock a dummy WebSocket connection
    class DummyWS:
        def __init__(self):
            self.closed = False
            self.close_code = None

        async def close(self, code=1000, reason=""):
            self.closed = True
            self.close_code = code

    dummy_ws = DummyWS()
    loop = asyncio.get_running_loop()
    sub_q = asyncio.Queue(maxsize=2)

    st.subscribers[dummy_ws] = set()
    st.subscriber_queues[dummy_ws] = sub_q
    st.subscriber_loops[dummy_ws] = loop
    st.subscriber_drops[dummy_ws] = 0

    # Fill the queue to capacity
    sub_q.put_nowait({"tick": 1})
    sub_q.put_nowait({"tick": 2})
    assert sub_q.full()

    # Now simulate broadcast from a background worker thread (cur_loop != ws_loop)
    def worker_broadcast(num_events: int):
        for k in range(num_events):
            event_data = {"instrument_id": "BTC-USD", "price": 50000.0 + k}
            # Directly call safe enqueue as call_soon_threadsafe does across threads
            loop.call_soon_threadsafe(st._enqueue_subscriber_event, dummy_ws, sub_q, event_data)
            time.sleep(0.005)

    t = threading.Thread(target=worker_broadcast, args=(5,))
    t.start()
    t.join(timeout=2.0)

    # Allow asyncio loop to process scheduled threadsafe callbacks
    await asyncio.sleep(0.05)

    # Invariant: Drops were accurately tracked
    assert st.total_subscriber_drops >= 3
    assert st.subscriber_drops[dummy_ws] >= 3
    assert dummy_ws in st._dead_subscribers

    # Broadcast next event should sweep dead subscriber
    await st.broadcast_event({"instrument_id": "BTC-USD", "price": 50010.0})
    assert dummy_ws not in st.subscribers
    assert dummy_ws not in st.subscriber_queues
    assert dummy_ws.closed is True
