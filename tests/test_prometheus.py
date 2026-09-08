"""
Unit & Integration Tests for MDRAP Prometheus Metrics Exporter (Spec §19, §26).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.request
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType
from prometheus import PrometheusMetricsServer, format_prometheus_metrics
from storage import Store


def test_format_prometheus_metrics_empty():
    """Verify metrics format outputs valid Prometheus 0.0.4 text with fallback defaults."""
    temp_dir = tempfile.mkdtemp()
    try:
        empty_db = os.path.join(temp_dir, "nonexistent.db")
        text = format_prometheus_metrics(store_path=empty_db, duckdb_path=empty_db)
        assert "# HELP mdrap_up" in text
        assert "# TYPE mdrap_up gauge" in text
        assert "mdrap_up 1" in text
        assert "mdrap_scrape_timestamp_seconds" in text
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_format_prometheus_metrics_with_store():
    """Verify metrics correctly reflect SQLite store counts, quarantine, and lineage."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_prom.db")
    try:
        store = Store(db_path)
        events = [
            CanonicalEvent(
                event_id=f"e_{i}",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0 + i,
                receive_timestamp=1000.001 + i,
                processing_timestamp=1000.002 + i,
                source="FEED_A",
                sequence_number=i,
                price=150.0 + i,
                quantity=10.0,
            )
            for i in range(5)
        ]
        store.write_canonical_batch(events)
        store.commit()
        store.close()

        text = format_prometheus_metrics(
            store_path=db_path,
            custom_metrics={"processed": 500, "throughput_eps": 15200.5, "e2e_p50_us": 12.3},
        )
        assert 'mdrap_canonical_ticks_total{status="VALID"} 5' in text
        assert "mdrap_events_processed_total 500" in text
        assert "mdrap_throughput_events_per_second 15200.5" in text
        assert 'mdrap_e2e_latency_microseconds{quantile="0.50"} 12.3' in text
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_prometheus_http_server_endpoints():
    """Verify PrometheusMetricsServer background daemon serves /metrics and /health."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_server.db")
    try:
        store = Store(db_path)
        store.close()

        server = PrometheusMetricsServer(host="127.0.0.1", port=9199, store_path=db_path)
        server.start()
        time.sleep(0.2)

        try:
            # 1. Scrape /metrics endpoint
            with urllib.request.urlopen("http://127.0.0.1:9199/metrics", timeout=2.0) as resp:
                assert resp.status == 200
                content_type = resp.headers.get("Content-Type", "")
                assert "text/plain" in content_type
                body = resp.read().decode("utf-8")
                assert "mdrap_up 1" in body

            # 2. Scrape /health endpoint
            with urllib.request.urlopen("http://127.0.0.1:9199/health", timeout=2.0) as resp:
                assert resp.status == 200
                content_type = resp.headers.get("Content-Type", "")
                assert "application/json" in content_type
                body = json.loads(resp.read().decode("utf-8"))
                assert body["status"] == "UP"
                assert body["service"] == "MDRAP"
        finally:
            server.stop()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
