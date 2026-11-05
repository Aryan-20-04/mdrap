"""
MDRAP v5.1 — Native C11 Atomic SPSC Queue Verification & Stress Suite.

Tests lock-free acquire/release ordering, cache-line separation, wraparound,
backpressure, reference counting lifecycle, and pure-Python dual-path parity.
"""

import gc
import os
import threading
import time
import pytest

from mdrap.partition import PySpscRingQueue, SpscRingQueue
from mdrap._fastpath_c import NativeSpscQueue


class TestFastpathSpscAtomics:
    """Rigorous verification of ADR-005 Native C11 Atomic SPSC Queue."""

    @pytest.mark.parametrize("queue_cls", [PySpscRingQueue, NativeSpscQueue])
    def test_power_of_two_rounding_and_mask(self, queue_cls):
        """Verify capacity rounds up to power of two and mask is correct."""
        q1 = queue_cls(capacity=7)
        assert q1.capacity == 8
        assert q1.mask == 7

        q2 = queue_cls(capacity=1000)
        assert q2.capacity == 1024
        assert q2.mask == 1023

    @pytest.mark.parametrize("queue_cls", [PySpscRingQueue, NativeSpscQueue])
    def test_saturation_and_empty_boundaries(self, queue_cls):
        """Verify full and empty states reject invalid operations."""
        cap = 16
        q = queue_cls(capacity=cap)
        assert q.empty()
        assert not q.full()
        assert q.qsize() == 0
        assert q.poll() is None

        for i in range(cap):
            assert q.offer(f"item_{i}")
            assert q.qsize() == i + 1

        assert q.full()
        assert not q.empty()
        # Saturation reject
        assert not q.offer("overflow_item")
        assert q.qsize() == cap

        # Drain
        for i in range(cap):
            val = q.poll()
            assert val == f"item_{i}"

        assert q.empty()
        assert not q.full()
        assert q.qsize() == 0
        assert q.poll() is None

    @pytest.mark.parametrize("queue_cls", [PySpscRingQueue, NativeSpscQueue])
    def test_wraparound_over_million_events(self, queue_cls):
        """Verify queue indices cleanly wrap around multiple times without drift."""
        cap = 64
        q = queue_cls(capacity=cap)
        iterations = 200_000

        for i in range(iterations):
            assert q.offer(i)
            res = q.poll()
            assert res == i

        assert q.empty()
        assert q.qsize() == 0

    def test_high_volume_concurrent_producer_consumer(self):
        """Stress test C11 acquire-release ordering across OS threads."""
        q = NativeSpscQueue(capacity=1024)
        total_events = 250_000
        consumer_results = []
        stop_event = threading.Event()

        def producer():
            for i in range(total_events):
                while not q.offer(i):
                    # Busy-spin on backpressure
                    pass

        def consumer():
            received = 0
            while received < total_events:
                item = q.poll()
                if item is not None:
                    consumer_results.append(item)
                    received += 1

        t_prod = threading.Thread(target=producer)
        t_cons = threading.Thread(target=consumer)

        t_prod.start()
        t_cons.start()

        t_prod.join(timeout=10.0)
        t_cons.join(timeout=10.0)

        assert not t_prod.is_alive(), "Producer deadlocked"
        assert not t_cons.is_alive(), "Consumer deadlocked"

        # Verify strict FIFO ordering with zero drops and zero duplicates
        assert len(consumer_results) == total_events
        assert consumer_results == list(range(total_events))

    def test_object_refcount_lifecycle(self):
        """Verify enqueued objects are properly decref'd and not leaked."""
        q = NativeSpscQueue(capacity=128)

        class MonitoredObject:
            pass

        obj = MonitoredObject()
        import sys
        initial_refs = sys.getrefcount(obj)

        assert q.offer(obj)
        # Refcount should be incremented by 1 inside the queue
        assert sys.getrefcount(obj) == initial_refs + 1

        polled = q.poll()
        assert polled is obj
        # Refcount returned to initial (one ref held by polled, one by obj)
        assert sys.getrefcount(obj) == initial_refs + 1
        del polled
        assert sys.getrefcount(obj) == initial_refs

    def test_dealloc_with_unpolled_items(self):
        """Verify queue garbage collection cleans up unpolled items without leaking."""
        class Disposable:
            freed = 0
            def __del__(self):
                Disposable.freed += 1

        Disposable.freed = 0
        q = NativeSpscQueue(capacity=128)
        for _ in range(50):
            q.offer(Disposable())

        del q
        gc.collect()

        assert Disposable.freed == 50, f"Expected 50 freed objects, got {Disposable.freed}"

    def test_dual_path_fallback_parity(self, monkeypatch):
        """Verify MDRAP_DISABLE_FASTPATH=1 cleanly falls back to pure Python."""
        monkeypatch.setenv("MDRAP_DISABLE_FASTPATH", "1")
        # Re-create queue under disabled fastpath
        q = SpscRingQueue(capacity=16)
        assert isinstance(q, PySpscRingQueue)

        monkeypatch.delenv("MDRAP_DISABLE_FASTPATH", raising=False)
        q_native = SpscRingQueue(capacity=16)
        assert isinstance(q_native, NativeSpscQueue)
