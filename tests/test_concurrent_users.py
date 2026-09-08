"""
Unit & Integration Tests for Concurrent Multi-Device Workload Simulation (§26).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workload_simulator import (
    ConcurrentWorkloadSimulator,
    DeviceConfig,
    DeviceResult,
    UserArchetype,
    _compute_percentile,
    run_device_worker,
)
from service import MarketDataDaemon, StreamClient
from prometheus import PrometheusMetricsServer


def test_device_result_percentiles_calculation():
    """Verify high-resolution percentile computation for device operations."""
    res = DeviceResult(device_id="test-dev", archetype="FAST_PACED_BOT")
    res.latencies_ms = [float(i) for i in range(1, 101)]  # 1.0 to 100.0 ms
    res.calculate_percentiles()

    assert res.p50_ms == 51.0
    assert res.p90_ms == 91.0
    assert res.p95_ms == 96.0
    assert res.p99_ms == 100.0
    assert res.max_ms == 100.0
    assert res.avg_ms == 50.5


def test_concurrent_normal_and_fast_devices():
    """Verify simultaneous execution of Normal User and Fast-Paced Bot against live daemon."""
    port = 20881
    prom_port = 20111
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        duck_path = os.path.join(tmpdir, "test.duckdb")

        daemon = MarketDataDaemon(
            host="127.0.0.1",
            port=port,
            db_path=db_path,
            enable_shm=False,
            use_live=False,
            sim_events=0,
            sim_speed_eps=5000.0,
            require_auth=False,
        )
        daemon.start(blocking=False)
        time.sleep(0.3)

        prom_srv = PrometheusMetricsServer(
            host="127.0.0.1",
            port=prom_port,
            store_path=db_path,
            duckdb_path=duck_path,
        )
        prom_srv.start()
        time.sleep(0.15)

        try:
            # Configure 1 normal user + 1 fast-paced bot
            cfg_normal = DeviceConfig(
                device_id="normal-01",
                archetype=UserArchetype.NORMAL_USER,
                port=port,
                prom_port=prom_port,
                duckdb_path=duck_path,
                duration_s=1.5,
            )
            cfg_fast = DeviceConfig(
                device_id="fast-01",
                archetype=UserArchetype.FAST_PACED_BOT,
                port=port,
                prom_port=prom_port,
                duckdb_path=duck_path,
                duration_s=1.5,
            )

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f_norm = executor.submit(run_device_worker, cfg_normal)
                f_fast = executor.submit(run_device_worker, cfg_fast)
                res_normal = f_norm.result(timeout=5.0)
                res_fast = f_fast.result(timeout=5.0)

            # Assertions
            assert res_normal.total_ops > 0
            assert res_fast.total_ops > 0
            assert len(res_normal.errors) == 0, f"Normal user errors: {res_normal.errors}"
            assert len(res_fast.errors) == 0, f"Fast user errors: {res_fast.errors}"
            # Fast bot should achieve significantly higher operation count than human-paced user
            assert res_fast.total_ops > res_normal.total_ops
            assert "BBO_QUOTE" in res_normal.op_breakdown or "STATUS" in res_normal.op_breakdown
            assert "L2_DEPTH_LADDER" in res_fast.op_breakdown or "VWAP_CURVE" in res_fast.op_breakdown
            assert res_fast.p50_ms > 0.0

        finally:
            prom_srv.stop()
            daemon.stop()


def test_workload_simulator_tier_orchestration():
    """Verify ConcurrentWorkloadSimulator runs a complete tier and renders reports."""
    port = 20882
    prom_port = 20112
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        duck_path = os.path.join(tmpdir, "test.duckdb")

        sim = ConcurrentWorkloadSimulator(
            db_path=db_path,
            duckdb_path=duck_path,
            port=port,
            prom_port=prom_port,
            sim_speed_eps=4000.0,
        )

        try:
            sim.start_services()
            rep = sim.run_tier(normal_count=2, fast_count=2, monitor_count=1, duration_s=1.5, mode="thread")

            assert rep["total_devices"] == 5
            assert rep["total_ops"] > 0
            assert rep["total_errors"] == 0
            assert "overall_latency" in rep
            assert rep["overall_latency"]["p50_ms"] >= 0.0
            assert "operation_breakdown" in rep
            assert len(rep["device_results"]) == 5

        finally:
            sim.stop_services()
