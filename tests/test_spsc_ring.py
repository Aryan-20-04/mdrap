import os
import sys
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from spsc_ring import SPSCRingBuffer, _next_power_of_two


def test_power_of_two_calculation():
    assert _next_power_of_two(1) == 1
    assert _next_power_of_two(2) == 2
    assert _next_power_of_two(3) == 4
    assert _next_power_of_two(15) == 16
    assert _next_power_of_two(1000) == 1024
    assert _next_power_of_two(16384) == 16384


def test_spsc_basic_offer_poll():
    buf = SPSCRingBuffer(capacity=16)
    assert buf.capacity == 16
    assert buf.is_empty()
    assert not buf.is_full()
    assert buf.size() == 0

    assert buf.offer("tick-1") is True
    assert buf.offer("tick-2") is True
    assert buf.offer("tick-3") is True
    assert buf.size() == 3

    assert buf.poll() == "tick-1"
    assert buf.poll() == "tick-2"
    assert buf.poll() == "tick-3"
    assert buf.poll() is None
    assert buf.is_empty()


def test_spsc_full_buffer_rejection():
    buf = SPSCRingBuffer(capacity=16)
    for i in range(16):
        assert buf.offer(f"item-{i}") is True

    assert buf.is_full()
    assert buf.size() == 16
    # 17th item rejected with drop tracking
    assert buf.offer_or_drop("overflow") is False
    assert buf.total_dropped == 1

    # Drain one, then offer succeeds
    assert buf.poll() == "item-0"
    assert buf.offer("overflow") is True
    assert buf.size() == 16


def test_spsc_drain_into():
    buf = SPSCRingBuffer(capacity=32)
    for i in range(25):
        buf.offer(f"evt-{i}")

    target = []
    drained = buf.drain_into(target, max_items=10)
    assert drained == 10
    assert len(target) == 10
    assert target[0] == "evt-0"
    assert target[9] == "evt-9"
    assert buf.size() == 15

    drained_rest = buf.drain_into(target, max_items=50)
    assert drained_rest == 15
    assert len(target) == 25
    assert target[-1] == "evt-24"
    assert buf.is_empty()


def test_spsc_concurrent_producer_consumer_integrity():
    """Verify thread-safe, lock-free streaming of 100,000 items between 2 concurrent OS threads."""
    buf = SPSCRingBuffer(capacity=8192)
    total_items = 100_000
    received = []
    consumer_done = threading.Event()

    def _producer():
        for i in range(total_items):
            while not buf.offer(i):
                # Spin/yield on buffer full
                time.sleep(0.00001)

    def _consumer():
        batch = []
        while len(received) < total_items:
            count = buf.drain_into(batch, max_items=1000)
            if count > 0:
                received.extend(batch)
                batch.clear()
            else:
                time.sleep(0.00001)
        consumer_done.set()

    t_prod = threading.Thread(target=_producer, name="spsc-producer")
    t_cons = threading.Thread(target=_consumer, name="spsc-consumer")

    t0 = time.perf_counter()
    t_cons.start()
    t_prod.start()

    t_prod.join(timeout=5.0)
    t_cons.join(timeout=5.0)

    elapsed = time.perf_counter() - t0

    assert len(received) == total_items
    # Verify strict FIFO sequence ordering with zero corruptions
    assert received == list(range(total_items))
    assert buf.total_dropped == 0
    throughput = total_items / elapsed
    assert throughput > 50_000, f"Expected >50k ops/sec, got {throughput:,.0f} ops/sec"


def test_spsc_stats():
    buf = SPSCRingBuffer(capacity=64)
    buf.offer("a")
    buf.offer("b")
    st = buf.stats()
    assert st["capacity"] == 64
    assert st["size"] == 2
    assert st["total_pushed"] == 2
    assert st["total_popped"] == 0
