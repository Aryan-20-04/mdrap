"""Unit and Integration tests for MDRAP Phase 6 Partitioning, Scaling, and Fan-Out Routing."""

import time
import pytest
from src.partition import (
    ShardConfig,
    SymbolPartitioner,
    ConsumerFanoutManager,
    TenantQuotaManager,
    FleetCoordinator,
)


def test_symbol_partitioner_range_and_hash():
    """Verify range-based and uniform hash-based symbol partitioning."""
    # 2 shards, range mode: A-L (Shard 0) and M-Z (Shard 1)
    partitioner = SymbolPartitioner(num_shards=2, mode="range")
    assert partitioner.get_shard_id("AAPL") == 0
    assert partitioner.get_shard_id("GOOGL") == 0
    assert partitioner.get_shard_id("MSFT") == 1
    assert partitioner.get_shard_id("TSLA") == 1
    assert partitioner.get_shard_id("NVDA") == 1

    # 4 shards, hash mode
    hash_part = SymbolPartitioner(num_shards=4, mode="hash")
    id1 = hash_part.get_shard_id("AAPL")
    id2 = hash_part.get_shard_id("AAPL")
    assert id1 == id2  # Deterministic
    assert 0 <= id1 < 4


def test_partitioned_routing_and_isolation():
    """Verify that events are routed to independent shards with isolated sequence numbers."""
    shards = [
        ShardConfig(shard_id=0, name="Shard_A_L", symbol_prefix_start="A", symbol_prefix_end="L"),
        ShardConfig(shard_id=1, name="Shard_M_Z", symbol_prefix_start="M", symbol_prefix_end="Z"),
    ]
    partitioner = SymbolPartitioner(num_shards=2, mode="range")
    fleet = FleetCoordinator(partitioner=partitioner, shard_configs=shards)
    fleet.start_fleet()

    try:
        # Route 10 events for AAPL (Shard 0) and 5 for MSFT (Shard 1)
        for i in range(10):
            sid, ok = fleet.dispatch_event("AAPL", f"aapl_event_{i}".encode("utf-8"))
            assert sid == 0
            assert ok is True

        for i in range(5):
            sid, ok = fleet.dispatch_event("MSFT", f"msft_event_{i}".encode("utf-8"))
            assert sid == 1
            assert ok is True

        # Allow worker threads to process
        time.sleep(0.1)

        health = fleet.fleet_health()
        assert health["fleet_status"] == "HEALTHY"
        assert health["shards"]["shard_0"]["total_processed"] == 10
        assert health["shards"]["shard_0"]["sequence_head"] == 10
        assert health["shards"]["shard_1"]["total_processed"] == 5
        assert health["shards"]["shard_1"]["sequence_head"] == 5
        assert health["aggregate_events_processed"] == 15
    finally:
        fleet.stop_fleet()


def test_consumer_fanout_and_noisy_neighbor_eviction():
    """Verify that a slow consumer is evicted without degrading a healthy peer consumer."""
    fanout = ConsumerFanoutManager(max_buffer_per_client=5)

    # Consumer A: fast reader
    session_fast = fanout.register_consumer("fast_desk", "TENANT_ALPHA")
    # Consumer B: slow consumer (doesn't read from queue)
    session_slow = fanout.register_consumer("slow_desk", "TENANT_BETA")

    # Broadcast 20 events
    for i in range(20):
        fanout.broadcast_event("AAPL", f"tick_{i}".encode("utf-8"))
        # Fast consumer drains queue immediately
        try:
            session_fast.stream_queue.get_nowait()
        except Exception:
            pass

    stats = fanout.stats()
    # Fast consumer should still be active and received all events
    assert "fast_desk" in stats["clients"]
    assert stats["clients"]["fast_desk"]["dispatched"] == 20

    # Slow consumer buffer was exceeded (> 5), drops accumulated >= 10, should be evicted!
    assert "slow_desk" not in stats["clients"]
    assert stats["total_evictions"] >= 1


def test_tenant_quota_governance():
    """Verify multi-tenant subscription and rate quota limits."""
    mgr = TenantQuotaManager(default_max_subscriptions=50, default_max_rate_eps=100)
    mgr.set_quota("TIER_VIP", max_subscriptions=500, max_rate_eps=5000)

    # Subscription quota check
    assert mgr.check_subscription_permitted("STANDARD_USER", 25) is True
    assert mgr.check_subscription_permitted("STANDARD_USER", 150) is False
    assert mgr.check_subscription_permitted("TIER_VIP", 300) is True

    # Rate limiting token bucket check
    # 50 ticks permitted within 100 limit
    assert mgr.record_and_check_rate("STANDARD_USER", count=50) is True
    # 60 more ticks exceeds 100 limit (50 + 60 = 110 > 100)
    assert mgr.record_and_check_rate("STANDARD_USER", count=60) is False
    # VIP user has 5000 limit, 1000 is permitted
    assert mgr.record_and_check_rate("TIER_VIP", count=1000) is True


def test_fleet_coordinator_aggregation():
    """Verify fleet health reporting across degraded and recovered shards."""
    shards = [
        ShardConfig(shard_id=0, name="Shard_A_L"),
        ShardConfig(shard_id=1, name="Shard_M_Z"),
    ]
    partitioner = SymbolPartitioner(num_shards=2, mode="range")
    fleet = FleetCoordinator(partitioner=partitioner, shard_configs=shards)

    # Before start, fleet is degraded
    health = fleet.fleet_health()
    assert health["fleet_status"] == "DEGRADED"

    fleet.start_fleet()
    health = fleet.fleet_health()
    assert health["fleet_status"] == "HEALTHY"
    assert health["total_shards"] == 2

    fleet.stop_fleet()
