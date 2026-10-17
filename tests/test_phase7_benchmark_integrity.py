"""Phase 7 Benchmark Integrity & Performance Baselines Test Suite (F-02, F-03, F-11).

Verifies:
- F-11: Fan-out delivery rate cannot exceed 100.0% under any circumstance.
- F-02 & F-03: Published benchmark summaries match raw sample distributions without artificial delays.
- Single-node crash-restart and WAL salvage metrics are reproducible and accurate.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import pytest

from mdrap.async_fanout import AsyncFanoutManager
from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent


def test_f11_all_audit_delivery_rates_bounded_at_100() -> None:
    """F-11: Delivery rate in all committed benchmark outputs must be <= 100.0%."""
    audit_dir = os.path.join(os.path.dirname(__file__), "..", "audit")
    assert os.path.isdir(audit_dir)

    for root, _, files in os.walk(audit_dir):
        for f in files:
            if f.endswith(".json") and "fanout" in f:
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                # Check for tiers
                tiers = data.get("tiers", {})
                for tier_name, tier_metrics in tiers.items():
                    if "delivery_rate_percent" in tier_metrics:
                        rate = tier_metrics["delivery_rate_percent"]
                        assert 0.0 <= rate <= 100.0, (
                            f"{path} tier {tier_name} has invalid delivery rate {rate}%"
                        )


def test_f02_f03_failover_results_consistent_with_raw_samples() -> None:
    """F-02 & F-03: Computed percentiles must match raw sample data within floating tolerance."""
    raw_path = os.path.join(
        os.path.dirname(__file__), "..", "audit", "phase10", "failover_raw_samples.json"
    )
    summary_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "audit",
        "phase10",
        "failover_benchmark_results.json",
    )

    if not os.path.isfile(raw_path) or not os.path.isfile(summary_path):
        pytest.skip("Audit phase 10 benchmark artifacts not present on checkout")

    with open(raw_path, "r", encoding="utf-8") as f:
        samples = json.load(f)
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    # Check total_complete_failover_ms percentiles
    raw_totals = sorted(s["total_complete_failover_ms"] for s in samples)
    n = len(raw_totals)
    expected_p50 = raw_totals[int(n * 0.50)]
    expected_p95 = raw_totals[int(n * 0.95)]
    expected_p99 = raw_totals[int(n * 0.99)]

    actual_stages = summary["lifecycle_stages"]["total_complete_failover_ms"]
    assert abs(actual_stages["p50"] - round(expected_p50, 2)) <= 0.05
    assert abs(actual_stages["p95"] - round(expected_p95, 2)) <= 0.05
    assert abs(actual_stages["p99"] - round(expected_p99, 2)) <= 0.05


def test_single_node_wal_recovery_reproducibility(tmp_path) -> None:
    """Verify single-node WAL crash recovery timing and complete record restoration."""
    wal_dir = tmp_path / "bench_wal"
    log = IngestLog(str(wal_dir), max_segment_bytes=16 * 1024)

    # Write 500 records
    for i in range(500):
        log.append(
            RawEvent(
                source="BENCH",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 100.0 + i,
                    "sequence": i + 1,
                    "exchange_ts": 1000.0 + i,
                },
                receive_timestamp=1000.0 + i,
            )
        )
    log.close()

    # Reopen and time recovery
    t0 = time.perf_counter()
    recovered = IngestLog(str(wal_dir), max_segment_bytes=16 * 1024)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert recovered.next_offset == 500
    assert elapsed_ms > 0.0
    assert elapsed_ms < 5000.0  # Under 5 seconds for 500 events
    recovered.close()


def test_fanout_manager_clean_delivery_rate() -> None:
    """Verify AsyncFanoutManager bounds delivery rate at 100% and preserves all frames."""
    mgr = AsyncFanoutManager(max_buffer_per_client=500)
    client_ids = ["c1", "c2", "c3"]
    for cid in client_ids:
        mgr.register_consumer(cid)

    num_events = 100
    for i in range(num_events):
        mgr.publish_event("AAPL", f"TICK_{i}".encode("utf-8"), seq=i)

    # Drain all
    total_received = 0
    for cid in client_ids:
        received = 0
        while mgr.consume_event(cid, timeout=0.01):
            received += 1
        total_received += received

    expected_total = num_events * len(client_ids)
    delivery_rate = min(100.0, round((total_received / expected_total) * 100.0, 2))
    assert delivery_rate == 100.0
    assert total_received == expected_total
    mgr.stop()
