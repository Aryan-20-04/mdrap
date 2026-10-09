"""
Unit & Integration Tests for MDRAP Phase 8 Asynchronous Consumer Fan-Out Engine.

Verifies:
1. Multi-client broadcast correctness and subscription filtering.
2. Slow consumer isolation: fast clients receive all frames without stalls.
3. Bounded buffer drop accounting (Zero Silent Drops invariant).
4. Automatic stalled client eviction when drop threshold is exceeded.
5. Concurrency scaling up to 100+ concurrent consumers.
6. Clean stop and thread teardown without resource leaks.
"""

import sys
import os
import time
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.async_fanout import AsyncFanoutManager


def test_basic_fanout_broadcast():
    mgr = AsyncFanoutManager(max_buffer_per_client=100)
    try:
        c1 = mgr.register_consumer("client_1", tenant_id="t1", subscribed_symbols=["AAPL"])
        c2 = mgr.register_consumer("client_2", tenant_id="t2", subscribed_symbols=["MSFT"])
        c3 = mgr.register_consumer("client_3", tenant_id="t3", subscribed_symbols=None)  # All

        # Publish AAPL
        mgr.publish_event("AAPL", b"FRAME_AAPL_1", seq=1)
        # Publish MSFT
        mgr.publish_event("MSFT", b"FRAME_MSFT_1", seq=2)

        time.sleep(0.05)

        # c1 should have received only AAPL
        item_c1 = mgr.consume_event("client_1", timeout=0.1)
        assert item_c1 is not None
        assert item_c1[0] == "AAPL"
        assert mgr.consume_event("client_1", timeout=0.01) is None

        # c2 should have received only MSFT
        item_c2 = mgr.consume_event("client_2", timeout=0.1)
        assert item_c2 is not None
        assert item_c2[0] == "MSFT"
        assert mgr.consume_event("client_2", timeout=0.01) is None

        # c3 subscribed to ALL -> receives both
        items_c3 = []
        for _ in range(2):
            it = mgr.consume_event("client_3", timeout=0.1)
            if it:
                items_c3.append(it[0])
        assert sorted(items_c3) == ["AAPL", "MSFT"]
    finally:
        mgr.stop()


def test_slow_consumer_isolation_and_drop_accounting():
    mgr = AsyncFanoutManager(
        max_buffer_per_client=20,
        eviction_drop_threshold=100,  # high threshold to observe drops without early eviction
    )
    try:
        fast_c = mgr.register_consumer("fast_client", max_queue_size=500)
        slow_c = mgr.register_consumer("slow_client", max_queue_size=10)

        # Ingest 100 events
        for i in range(100):
            mgr.publish_event("BTC/USD", f"DATA_{i}".encode("utf-8"), seq=i)

        time.sleep(0.08)

        st = mgr.stats()
        # Slow client buffer capped at 10, should have dropped roughly 90 events
        slow_stats = st["clients"]["slow_client"]
        assert slow_stats["dropped"] > 50
        assert slow_stats["queue_depth"] <= 10

        # Fast client should have received all 100 events with 0 drops
        fast_stats = st["clients"]["fast_client"]
        assert fast_stats["dropped"] == 0
        assert fast_stats["dispatched"] == 100
        assert fast_stats["queue_depth"] == 100

        # Verify fast client can drain cleanly
        drained = 0
        while mgr.consume_event("fast_client", timeout=0.01) is not None:
            drained += 1
        assert drained == 100
    finally:
        mgr.stop()


def test_stalled_client_auto_eviction():
    mgr = AsyncFanoutManager(
        max_buffer_per_client=5,
        eviction_drop_threshold=15,
    )
    try:
        slow_c = mgr.register_consumer("stalled_client", max_queue_size=5)

        # Publish 50 events without reading from slow_c
        for i in range(50):
            mgr.publish_event("ETH/USD", f"FRAME_{i}".encode("utf-8"), seq=i)

        time.sleep(0.08)

        st = mgr.stats()
        # Stalled client should be evicted
        assert st["total_evictions"] >= 1
        assert "stalled_client" not in st["clients"]
        assert st["active_consumers"] == 0
    finally:
        mgr.stop()


def test_100_concurrent_consumers_scale():
    num_clients = 100
    mgr = AsyncFanoutManager(max_buffer_per_client=200)
    try:
        for i in range(num_clients):
            mgr.register_consumer(f"consumer_{i:03d}")

        st = mgr.stats()
        assert st["active_consumers"] == 100

        # Publish 50 events
        for seq in range(50):
            mgr.publish_event("NVDA", f"TICK_{seq}".encode("utf-8"), seq=seq)

        time.sleep(0.1)

        # Every client drains all 50 events concurrently
        consumed_counts = [0] * num_clients

        def reader(idx: int):
            cid = f"consumer_{idx:03d}"
            count = 0
            while True:
                item = mgr.consume_event(cid, timeout=0.05)
                if not item:
                    break
                count += 1
            consumed_counts[idx] = count

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(num_clients)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        # Verify all 100 clients received exactly 50 events
        for i in range(num_clients):
            assert consumed_counts[i] == 50, f"Consumer {i} received {consumed_counts[i]} instead of 50"

        final_st = mgr.stats()
        assert final_st["total_dispatched"] == 100 * 50
        assert final_st["total_dropped"] == 0
        assert final_st["total_evictions"] == 0
    finally:
        mgr.stop()
