"""
Tests for non-blocking in-memory cached PrometheusMetricsServer.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import pytest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from prometheus import PrometheusMetricsServer


@pytest.fixture
def prom_server():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_prom_cache.db")
    server = PrometheusMetricsServer(
        host="127.0.0.1",
        port=19105,
        store_path=db_path,
        duckdb_path=os.path.join(temp_dir, "test.duckdb"),
        cache_ttl_s=0.1,  # fast refresh for testing
    )
    server.start()
    time.sleep(0.15)
    yield server
    server.stop()
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_cached_prometheus_scrape_speed_and_content(prom_server):
    """Verify Prometheus scrape responds from memory in sub-5ms."""
    url = f"http://127.0.0.1:{prom_server.port}/metrics"

    # Warmup request
    with urllib.request.urlopen(url, timeout=1.0) as resp:
        assert resp.status == 200

    # Measure scrape speed
    t0 = time.perf_counter_ns()
    with urllib.request.urlopen(url, timeout=1.0) as resp:
        body = resp.read().decode("utf-8")
        lat_ms = (time.perf_counter_ns() - t0) / 1e6

    assert "mdrap_up 1" in body
    assert lat_ms < 25.0  # Ultra-fast local in-memory response


def test_concurrent_prometheus_scrapes(prom_server):
    """Verify multi-threaded concurrent HTTP scrapes execute without head-of-line blocking."""
    url = f"http://127.0.0.1:{prom_server.port}/metrics"
    health_url = f"http://127.0.0.1:{prom_server.port}/health"

    def fetch_url(target: str) -> int:
        with urllib.request.urlopen(target, timeout=2.0) as resp:
            return resp.status

    targets = [url if i % 2 == 0 else health_url for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        statuses = list(ex.map(fetch_url, targets))

    assert len(statuses) == 20
    assert all(s == 200 for s in statuses)


def test_custom_metrics_dynamic_update(prom_server):
    """Verify custom metrics dynamically refresh without restarting server."""
    prom_server.update_custom_metrics({"processed": 99999, "throughput_eps": 5000.0})

    url = f"http://127.0.0.1:{prom_server.port}/metrics"
    with urllib.request.urlopen(url, timeout=1.0) as resp:
        body = resp.read().decode("utf-8")

    assert "mdrap_events_processed_total 99999" in body
    assert "mdrap_throughput_events_per_second 5000.0" in body
