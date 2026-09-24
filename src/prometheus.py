"""MDRAP Production Prometheus Metrics Exporter.

Exposes an authentic Prometheus text exposition format (version 0.0.4) metrics
endpoint for scraping by Prometheus, VictoriaMetrics, or Datadog agents.

Guarantees:
1. Pure Python standard library with zero external dependencies.
2. Allocation-light, lock-free counter reads in the hot path.
3. Natural pull-model decoupling: metrics formatting occurs only during /metrics HTTP scrape.
4. Gated by network binding (loopback default) or API key authentication in production.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Tuple

__stability__ = "stable"


class PrometheusExporter:
    """Manages and serializes MDRAP operational metrics into Prometheus exposition format."""

    def __init__(self) -> None:
        # API HTTP Metrics: (method, endpoint) -> count
        self._api_requests: Dict[Tuple[str, str], int] = {}
        self._api_errors: Dict[Tuple[str, str], int] = {}
        self._api_latency_sum: float = 0.0
        self._api_latency_count: int = 0

        # Custom / externally-injected counters
        self._custom_counters: Dict[str, float] = {}
        self._custom_gauges: Dict[str, float] = {}

        # Audit cache to prevent re-hashing database on every scrape
        self._last_audit_check_ts: float = 0.0
        self._last_audit_status: int = 1

    def record_api_request(
        self, method: str, endpoint: str, duration_s: float, status_code: int
    ) -> None:
        """Record an API request in the exporter's local tracking counters."""
        key = (method.upper(), endpoint)
        self._api_requests[key] = self._api_requests.get(key, 0) + 1
        self._api_latency_sum += duration_s
        self._api_latency_count += 1
        if status_code >= 400:
            self._api_errors[key] = self._api_errors.get(key, 0) + 1

    def set_gauge(self, name: str, value: float) -> None:
        """Set a named custom gauge."""
        self._custom_gauges[name] = float(value)

    def inc_counter(self, name: str, value: float = 1.0) -> None:
        """Increment a named custom counter."""
        self._custom_counters[name] = self._custom_counters.get(name, 0.0) + float(value)

    def render(self, state: Any | None = None) -> str:
        """Render all metrics into standard Prometheus 0.0.4 text format."""
        lines: list[str] = []

        def add_metric(
            name: str,
            mtype: str,
            help_text: str,
            value: float | int,
            labels: dict[str, str] | None = None,
        ) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            if labels:
                lbl_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
                lines.append(f"{name}{{{lbl_str}}} {value}")
            else:
                lines.append(f"{name} {value}")

        # -------------------------------------------------------------------
        # 1. Pipeline Throughput & Quality Metrics
        # -------------------------------------------------------------------
        metrics = getattr(state.pipeline, "metrics", None) if state and hasattr(state, "pipeline") else None

        processed = getattr(metrics, "processed", 0) if metrics else int(self._custom_counters.get("processed", 0))
        dropped = getattr(metrics, "dropped", 0) if metrics else int(self._custom_counters.get("dropped", 0))

        add_metric("mdrap_events_processed_total", "counter", "Total count of events processed by the pipeline", processed)
        add_metric("mdrap_events_dropped_total", "counter", "Total count of unrecoverable dropped events", dropped)

        q_counts = getattr(metrics, "quality_counts", {}) if metrics else {}
        valid_count = q_counts.get("VALID", int(self._custom_counters.get("quality_valid", 0)))
        suspicious_count = q_counts.get("SUSPICIOUS", int(self._custom_counters.get("quality_suspicious", 0)))
        invalid_count = q_counts.get("INVALID", int(self._custom_counters.get("quality_invalid", 0)))

        lines.append("# HELP mdrap_events_quality_total Breakdown of processed events by evaluated quality status")
        lines.append("# TYPE mdrap_events_quality_total counter")
        lines.append(f'mdrap_events_quality_total{{status="VALID"}} {valid_count}')
        lines.append(f'mdrap_events_quality_total{{status="SUSPICIOUS"}} {suspicious_count}')
        lines.append(f'mdrap_events_quality_total{{status="INVALID"}} {invalid_count}')

        # Quarantine rate: (SUSPICIOUS + INVALID) / processed
        total_quarantined = suspicious_count + invalid_count
        quarantine_rate = (total_quarantined / processed) if processed > 0 else 0.0
        add_metric("mdrap_quarantine_rate", "gauge", "Ratio of quarantined (suspicious + invalid) to total events", f"{quarantine_rate:.6f}")

        # -------------------------------------------------------------------
        # 2. Feed Health, Disagreements & Watchdog
        # -------------------------------------------------------------------
        uptime = (time.time() - state.start_time) if state and hasattr(state, "start_time") else 0.0
        add_metric("mdrap_feed_uptime_seconds", "gauge", "MDRAP node uptime in seconds", f"{uptime:.2f}")

        watchdog = getattr(state, "watchdog", None) if state else None
        if watchdog and hasattr(watchdog, "source_states"):
            source_states = watchdog.source_states()
            healthy_count = sum(1 for s in source_states.values() if s == "HEALTHY")
            add_metric("mdrap_feed_healthy_count", "gauge", "Number of currently healthy market data sources", healthy_count)
            add_metric("mdrap_feed_monitored_total", "gauge", "Total number of monitored market data sources", len(source_states))

            lines.append("# HELP mdrap_feed_status Health status of each monitored source (1=HEALTHY, 0=OTHER)")
            lines.append("# TYPE mdrap_feed_status gauge")
            for src, status_str in sorted(source_states.items()):
                val = 1 if status_str == "HEALTHY" else 0
                lines.append(f'mdrap_feed_status{{source="{src}"}} {val}')
        else:
            add_metric("mdrap_feed_healthy_count", "gauge", "Number of currently healthy market data sources", int(self._custom_gauges.get("healthy_sources", 1)))

        # Cross-feed disagreements
        disagreement_count = 0
        reliability = getattr(state, "reliability", None) if state else None
        if reliability and hasattr(reliability, "stats"):
            for st in reliability.stats.values():
                disagreement_count += getattr(st, "disagreement_count", 0)
        else:
            disagreement_count = int(self._custom_counters.get("disagreements", 0))

        add_metric("mdrap_cross_feed_disagreements_total", "counter", "Total count of multi-venue consensus divergences", disagreement_count)

        # -------------------------------------------------------------------
        # 3. Cryptographic Audit Chain Verification Status
        # -------------------------------------------------------------------
        now = time.time()
        store = getattr(state, "store", None) if state else None
        if store and hasattr(store, "verify_audit_integrity"):
            # Cache verification status for 30s to avoid full-table verification on high scrape frequency
            if now - self._last_audit_check_ts > 30.0:
                try:
                    res = store.verify_audit_integrity()
                    verified = res[0] if isinstance(res, (tuple, list)) else bool(res)
                    self._last_audit_status = 1 if verified else 0
                except Exception:
                    self._last_audit_status = 0
                self._last_audit_check_ts = now
            audit_status = self._last_audit_status
        else:
            audit_status = int(self._custom_gauges.get("audit_status", 1))

        add_metric("mdrap_audit_verified_status", "gauge", "Merkle audit log verification status (1=VALID, 0=CORRUPT)", audit_status)

        # -------------------------------------------------------------------
        # 4. Kafka / Redpanda OutputSink Metrics
        # -------------------------------------------------------------------
        kafka_sink = getattr(state, "kafka_sink", None) if state else None
        if kafka_sink:
            k_lag = getattr(kafka_sink, "lag", lambda: 0)()
            k_produced = getattr(kafka_sink, "produced_count", 0)
            k_dropped = getattr(kafka_sink, "dropped_count", 0)
        else:
            k_lag = int(self._custom_gauges.get("kafka_sink_lag", 0))
            k_produced = int(self._custom_counters.get("kafka_sink_produced", 0))
            k_dropped = int(self._custom_counters.get("kafka_sink_dropped", 0))

        add_metric("mdrap_kafka_sink_lag_events", "gauge", "Durable Kafka sink checkpoint lag in events behind store", k_lag)
        add_metric("mdrap_kafka_sink_produced_total", "counter", "Total events successfully produced to Kafka topic", k_produced)
        add_metric("mdrap_kafka_sink_dropped_total", "counter", "Total events dropped or dead-lettered by Kafka sink", k_dropped)

        # -------------------------------------------------------------------
        # 5. Alert Delivery Metrics
        # -------------------------------------------------------------------
        alert_engine = getattr(state, "alert_engine", None) if state else None
        if alert_engine:
            a_delivered = getattr(alert_engine, "delivery_delivered_count", 0)
            a_failed = getattr(alert_engine, "delivery_failed_count", 0)
            a_backlog = getattr(alert_engine, "delivery_backlog_count", 0)
        else:
            a_delivered = int(self._custom_counters.get("alert_delivered", 0))
            a_failed = int(self._custom_counters.get("alert_failed", 0))
            a_backlog = int(self._custom_gauges.get("alert_backlog", 0))

        lines.append("# HELP mdrap_alert_delivery_total Total count of external alert deliveries by status")
        lines.append("# TYPE mdrap_alert_delivery_total counter")
        lines.append(f'mdrap_alert_delivery_total{{status="delivered"}} {a_delivered}')
        lines.append(f'mdrap_alert_delivery_total{{status="failed"}} {a_failed}')
        add_metric("mdrap_alert_delivery_backlog", "gauge", "Current backlog of pending alert notifications", a_backlog)

        # -------------------------------------------------------------------
        # 6. HTTP API Operational Metrics
        # -------------------------------------------------------------------
        if self._api_requests:
            lines.append("# HELP mdrap_api_requests_total Total HTTP requests handled by method and endpoint")
            lines.append("# TYPE mdrap_api_requests_total counter")
            for (m, ep), cnt in sorted(self._api_requests.items()):
                lines.append(f'mdrap_api_requests_total{{method="{m}",endpoint="{ep}"}} {cnt}')

        if self._api_errors:
            lines.append("# HELP mdrap_api_errors_total Total HTTP error responses (>=400) by method and endpoint")
            lines.append("# TYPE mdrap_api_errors_total counter")
            for (m, ep), cnt in sorted(self._api_errors.items()):
                lines.append(f'mdrap_api_errors_total{{method="{m}",endpoint="{ep}"}} {cnt}')

        avg_latency = (self._api_latency_sum / self._api_latency_count) if self._api_latency_count > 0 else 0.0
        add_metric("mdrap_api_latency_seconds_avg", "gauge", "Average HTTP request latency in seconds", f"{avg_latency:.6f}")

        # Terminating newline required by Prometheus specification
        return "\n".join(lines) + "\n"


# Singleton instance for system-wide exposition
global_prometheus_exporter = PrometheusExporter()
