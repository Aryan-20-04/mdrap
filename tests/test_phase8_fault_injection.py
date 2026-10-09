"""
Fault Injection & Chaos Resilience Test Suite for MDRAP Phase 8.

Injects adversarial network, consensus, and backpressure failure modes:
1. FAULT-01: Stalled consumer buffer overflow -> non-blocking drop with counter.
2. FAULT-02: Stalled consumer noisy-neighbor auto-eviction.
3. FAULT-03: Stale primary writer attempting post-failover WAL write -> FencingTokenError.
4. FAULT-04: Network partition isolating leader -> QuorumLossError and write halt.
5. FAULT-05: Expired lease token -> write rejected before persistence.
6. FAULT-06: Incoming fan-out buffer burst saturation -> bounded memory preserved.
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.async_fanout import AsyncFanoutManager
from mdrap.consensus import (
    ConsensusCoordinator,
    FencedWALWriter,
    FencingTokenError,
    QuorumLossError,
)


def test_fault_01_stalled_consumer_overflow():
    mgr = AsyncFanoutManager(max_buffer_per_client=10, eviction_drop_threshold=100)
    try:
        c = mgr.register_consumer("stalled_c", max_queue_size=10)
        for i in range(50):
            mgr.publish_event("AAPL", b"TICK", seq=i)
        time.sleep(0.05)
        st = mgr.stats()
        assert st["clients"]["stalled_c"]["dropped"] >= 40
        assert st["clients"]["stalled_c"]["queue_depth"] <= 10
    finally:
        mgr.stop()


def test_fault_02_noisy_neighbor_auto_eviction():
    mgr = AsyncFanoutManager(max_buffer_per_client=5, eviction_drop_threshold=10)
    try:
        mgr.register_consumer("bad_c", max_queue_size=5)
        for i in range(30):
            mgr.publish_event("AAPL", b"TICK", seq=i)
        time.sleep(0.05)
        st = mgr.stats()
        assert st["total_evictions"] >= 1
        assert "bad_c" not in st["clients"]
    finally:
        mgr.stop()


def test_fault_03_stale_leader_fencing():
    writer = FencedWALWriter("partition_EQUITIES")
    nodes = ["n1", "n2", "n3"]
    c1 = ConsensusCoordinator("n1", nodes, lease_duration_sec=1.0)
    c2 = ConsensusCoordinator("n2", nodes, lease_duration_sec=1.0)

    t1 = c1.request_leadership()
    writer.validate_write(t1)

    c2._current_epoch = 1
    t2 = c2.request_leadership()
    writer.validate_write(t2)

    with pytest.raises(FencingTokenError):
        writer.validate_write(t1)


def test_fault_04_network_partition_quorum_loss():
    nodes = ["n1", "n2", "n3"]
    c1 = ConsensusCoordinator("n1", nodes, lease_duration_sec=0.5)
    c1.simulate_network_partition(["n2", "n3"])

    with pytest.raises(QuorumLossError):
        c1.request_leadership()


def test_fault_05_lease_expiration_write_rejection():
    writer = FencedWALWriter("partition_BONDS")
    nodes = ["n1", "n2", "n3"]
    c1 = ConsensusCoordinator("n1", nodes, lease_duration_sec=0.04)

    t1 = c1.request_leadership()
    time.sleep(0.06)

    with pytest.raises(FencingTokenError):
        writer.validate_write(t1)


def test_fault_06_incoming_buffer_saturation():
    mgr = AsyncFanoutManager(max_incoming_buffer=100)
    try:
        # Pause dispatcher temporarily to saturate incoming buffer
        mgr._running = False
        with mgr._incoming_cond:
            mgr._incoming_cond.notify_all()
        mgr._dispatcher_thread.join(timeout=0.5)

        for i in range(250):
            mgr.publish_event("AAPL", b"TICK", seq=i)

        st = mgr.stats()
        assert st["incoming_queue_depth"] <= 100
        assert st["total_dropped"] >= 150
    finally:
        pass
