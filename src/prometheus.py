"""
Zero-Dependency Prometheus Metrics Exporter & HTTP Server for MDRAP (Spec §19, §26).

Exposes platform telemetry in standard Prometheus text exposition format (version 0.0.4)
over a lightweight stdlib HTTP server for scraping by Prometheus, Grafana, Datadog,
or custom quantitative execution algorithms without external framework dependencies.
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from storage import Store


def format_prometheus_metrics(
    store_path: str = "data/mdrap.db",
    duckdb_path: str = "data/mdrap.duckdb",
    custom_metrics: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate Prometheus text exposition format string from platform state.
    """
    lines: List[str] = []

    def _metric(name: str, doc: str, mtype: str, value: Any, labels: Optional[Dict[str, str]] = None):
        lines.append(f"# HELP {name} {doc}")
        lines.append(f"# TYPE {name} {mtype}")
        lbl_str = ""
        if labels:
            pairs = [f'{k}="{v}"' for k, v in sorted(labels.items())]
            lbl_str = "{" + ",".join(pairs) + "}"
        lines.append(f"{name}{lbl_str} {value}")

    # Process & system metrics
    _metric("mdrap_up", "Whether the MDRAP platform metrics exporter is operational", "gauge", 1)
    _metric("mdrap_scrape_timestamp_seconds", "Current epoch timestamp of metric generation", "gauge", f"{time.time():.4f}")

    # SQLite Store metrics
    if os.path.exists(store_path) and os.path.getsize(store_path) > 0:
        try:
            store = Store(store_path)
            counts = store.counts()
            for status in ("VALID", "SUSPICIOUS", "INVALID"):
                count = counts.get(status, 0)
                _metric(
                    "mdrap_canonical_ticks_total",
                    "Total canonical market data ticks by quality status",
                    "counter",
                    count,
                    {"status": status},
                )

            # Quarantine count
            cur = store.conn.execute("SELECT count(*) FROM quarantine")
            quar_count = cur.fetchone()[0]
            _metric("mdrap_quarantine_ticks_total", "Total quarantined invalid market events", "counter", quar_count)

            # Lineage records
            cur = store.conn.execute("SELECT count(*) FROM lineage")
            lin_count = cur.fetchone()[0]
            _metric("mdrap_lineage_records_total", "Total source lineage tracking entries", "counter", lin_count)

            # Source reliability stats
            try:
                for row in store.conn.execute("SELECT source, total, error_rate, duplicate, ewma_latency_s, score FROM source_stats"):
                    src = str(row[0])
                    _metric("mdrap_source_reliability_score", "Reliability score (0.0 to 1.0)", "gauge", f"{row[5]:.4f}", {"source": src})
                    _metric("mdrap_source_error_rate_pct", "Source error rate percentage", "gauge", f"{row[2] * 100.0:.2f}", {"source": src})
                    _metric("mdrap_source_ewma_latency_seconds", "EWMA latency of source in seconds", "gauge", f"{row[4]:.6f}", {"source": src})
            except Exception:
                pass

            # BBO records
            try:
                cur = store.conn.execute("SELECT count(*) FROM bbo")
                bbo_count = cur.fetchone()[0]
                _metric("mdrap_bbo_records_total", "Total consolidated Best Bid and Offer snapshots", "counter", bbo_count)
            except Exception:
                pass

            store.close()
        except Exception as exc:
            lines.append(f"# Error reading SQLite store: {exc}")

    # DuckDB Columnar & CDC Freshness metrics
    if os.path.exists(duckdb_path):
        try:
            from columnar import ColumnarStore
            with ColumnarStore(db_path=duckdb_path, read_only=True) as col:
                col_ticks = col.count()
                _metric("mdrap_columnar_ticks_total", "Total ticks stored in DuckDB columnar store", "gauge", col_ticks)
                if os.path.exists(store_path):
                    fresh = col.freshness(store_path)
                    _metric("mdrap_cdc_replication_lag_ticks", "Number of SQLite ticks pending replication to DuckDB", "gauge", fresh["lag_ticks"])
                    _metric("mdrap_cdc_is_fresh", "1 if DuckDB is fully in-sync with SQLite, 0 otherwise", "gauge", 1 if fresh["is_fresh"] else 0)
                _metric("mdrap_columnar_distinct_instruments", "Number of distinct financial instruments in columnar store", "gauge", len(col.symbols()))
        except Exception as exc:
            lines.append(f"# Error reading DuckDB store: {exc}")

    # Custom runtime metrics if provided (e.g. from live daemon / stream / quant engine)
    if custom_metrics:
        if "processed" in custom_metrics:
            _metric("mdrap_events_processed_total", "Total events processed in current session", "counter", custom_metrics["processed"])
        if "throughput_eps" in custom_metrics:
            _metric("mdrap_throughput_events_per_second", "Current ingestion throughput", "gauge", f"{custom_metrics['throughput_eps']:.1f}")
        if "e2e_p50_us" in custom_metrics:
            _metric("mdrap_e2e_latency_microseconds", "End-to-end pipeline latency quantiles", "gauge", f"{custom_metrics['e2e_p50_us']:.1f}", {"quantile": "0.50"})
        if "e2e_p95_us" in custom_metrics:
            _metric("mdrap_e2e_latency_microseconds", "End-to-end pipeline latency quantiles", "gauge", f"{custom_metrics['e2e_p95_us']:.1f}", {"quantile": "0.95"})
        if "e2e_p99_us" in custom_metrics:
            _metric("mdrap_e2e_latency_microseconds", "End-to-end pipeline latency quantiles", "gauge", f"{custom_metrics['e2e_p99_us']:.1f}", {"quantile": "0.99"})
        if "proc_p50_us" in custom_metrics:
            _metric("mdrap_proc_latency_microseconds", "Compute processing latency quantiles", "gauge", f"{custom_metrics['proc_p50_us']:.1f}", {"quantile": "0.50"})
        if "proc_p95_us" in custom_metrics:
            _metric("mdrap_proc_latency_microseconds", "Compute processing latency quantiles", "gauge", f"{custom_metrics['proc_p95_us']:.1f}", {"quantile": "0.95"})
        if "proc_p99_us" in custom_metrics:
            _metric("mdrap_proc_latency_microseconds", "Compute processing latency quantiles", "gauge", f"{custom_metrics['proc_p99_us']:.1f}", {"quantile": "0.99"})

    lines.append("")
    return "\n".join(lines)


class _MetricsHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Zero-overhead HTTP Request Handler serving in-memory cached /metrics and /health."""

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        srv: Optional[PrometheusMetricsServer] = getattr(self.server, "metrics_server", None)

        if path in ("/metrics", "/"):
            data = srv._cached_metrics_bytes if srv else b""
            if not data:
                content = format_prometheus_metrics(
                    store_path=getattr(self.server, "store_path", "data/mdrap.db"),
                    duckdb_path=getattr(self.server, "duckdb_path", "data/mdrap.duckdb"),
                    custom_metrics=getattr(self.server, "custom_metrics", None),
                )
                data = content.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/health":
            data = srv._cached_health_bytes if srv else b""
            if not data:
                health = {
                    "status": "UP",
                    "service": "MDRAP",
                    "timestamp": time.time(),
                }
                data = json.dumps(health, indent=2).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard logging to keep terminal clean
        pass


class PrometheusMetricsServer:
    """
    High-performance non-blocking background HTTP server exposing Prometheus metrics.
    Caches formatted metrics in memory and refreshes asynchronously to guarantee
    sub-millisecond HTTP response times and eliminate disk/database contention.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9100,
        store_path: str = "data/mdrap.db",
        duckdb_path: str = "data/mdrap.duckdb",
        cache_ttl_s: float = 0.5,
    ):
        self.host = host
        self.port = port
        self.store_path = store_path
        self.duckdb_path = duckdb_path
        self.cache_ttl_s = cache_ttl_s
        self.custom_metrics: Dict[str, Any] = {}
        self._server: Optional[http.server.HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._collector_thread: Optional[threading.Thread] = None
        self._running = False
        self._cache_lock = threading.Lock()
        self._cached_metrics_bytes: bytes = b""
        self._cached_health_bytes: bytes = b""
        self._last_refresh_ts: float = 0.0

    def refresh_cache(self) -> None:
        """Asynchronously compute and pre-render Prometheus text format into byte buffers."""
        try:
            content = format_prometheus_metrics(
                store_path=self.store_path,
                duckdb_path=self.duckdb_path,
                custom_metrics=self.custom_metrics,
            )
            metrics_bytes = content.encode("utf-8")
            health = {
                "status": "UP",
                "service": "MDRAP",
                "timestamp": time.time(),
            }
            health_bytes = json.dumps(health, indent=2).encode("utf-8")
            with self._cache_lock:
                self._cached_metrics_bytes = metrics_bytes
                self._cached_health_bytes = health_bytes
                self._last_refresh_ts = time.time()
        except Exception:
            pass

    def _collector_loop(self) -> None:
        """Background loop updating in-memory metrics cache at fixed intervals."""
        while self._running:
            time.sleep(self.cache_ttl_s)
            if not self._running:
                break
            self.refresh_cache()

    def start(self) -> None:
        """Start the metrics HTTP server and background cache collector."""
        # 1. Pre-warm cache immediately before opening socket
        self.refresh_cache()
        self._running = True

        # 2. Start background collector thread
        self._collector_thread = threading.Thread(
            target=self._collector_loop,
            daemon=True,
            name="mdrap-prom-collector",
        )
        self._collector_thread.start()

        # 3. Start non-blocking multi-threaded HTTP server
        ServerClass = getattr(http.server, "ThreadingHTTPServer", http.server.HTTPServer)
        self._server = ServerClass((self.host, self.port), _MetricsHTTPHandler)
        self._server.store_path = self.store_path  # type: ignore
        self._server.duckdb_path = self.duckdb_path  # type: ignore
        self._server.custom_metrics = self.custom_metrics  # type: ignore
        self._server.metrics_server = self  # type: ignore

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="mdrap-prom-http")
        self._thread.start()

    def stop(self) -> None:
        """Stop the HTTP server and background collector."""
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._collector_thread and self._collector_thread.is_alive():
            self._collector_thread.join(timeout=1.0)
            self._collector_thread = None

    def update_custom_metrics(self, metrics: Dict[str, Any]) -> None:
        self.custom_metrics.update(metrics)
        self.refresh_cache()
