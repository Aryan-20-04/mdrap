"""MDRAP Phase 2 — Read-Only Deployment Security & Resource Overhead Profiler."""

from __future__ import annotations

import os
import psutil
import subprocess
import sys
import time
import pytest

from mdrap.models import RawEvent
from mdrap.gateway import normalize
from mdrap.quality import QualityEngine
from mdrap.storage import Store
from mdrap.provenance import EventProvenance, ProvenanceTracker, create_provenance
from tools.check_read_only_safety import audit_source_tree


def test_automated_read_only_ast_scan():
    """Verify that zero order-routing or trade execution code exists in src/mdrap."""
    files_scanned, violations = audit_source_tree("src/mdrap")
    assert files_scanned >= 100
    assert len(violations) == 0


def test_resource_consumption_and_cpu_overhead():
    """Profile CPU and RAM overhead over 10,000 ingested quotes to ensure sidecar safety."""
    process = psutil.Process(os.getpid())
    rss_initial_mb = process.memory_info().rss / (1024 * 1024)

    store = Store(":memory:")
    tracker = ProvenanceTracker(store=store)
    engine = QualityEngine()

    t0 = time.perf_counter()
    for i in range(10_000):
        raw = RawEvent(
            source="BINANCE",
            payload={
                "instrument": "BTC/USDT",
                "event_type": "QUOTE",
                "bid": 65000.0 + i * 0.01,
                "ask": 65000.5 + i * 0.01,
                "sequence": 1000 + i,
            },
            receive_timestamp=time.time(),
            raw_id=f"bench-{i}",
            wire='{"b":65000.0,"a":65000.5}',
        )
        canon = normalize(raw)
        engine.evaluate(canon)
        prov = create_provenance(raw, canon)
        tracker.record(prov)

    elapsed = time.perf_counter() - t0
    rss_final_mb = process.memory_info().rss / (1024 * 1024)
    rss_growth_mb = rss_final_mb - rss_initial_mb

    per_event_us = (elapsed / 10_000) * 1_000_000.0
    throughput_eps = 10_000 / elapsed

    print(f"\nThroughput: {throughput_eps:.0f} events/sec")
    print(f"Per-Event Processing Overhead: {per_event_us:.2f} µs")
    print(f"Memory RSS Growth: {rss_growth_mb:.2f} MB")

    # Sidecar overhead invariants:
    # 1. Processing must complete in < 250 µs per event in pure Python
    assert per_event_us < 250.0
    # 2. Total memory growth for 10,000 events in memory ring must be < 50 MB
    assert rss_growth_mb < 50.0
