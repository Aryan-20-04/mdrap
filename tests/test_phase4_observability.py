"""Phase 4 Operational Observability, Alerting & Metrics Test Suite (Workstream F).

Verifies operational telemetry:
  1. Prometheus text exposition format (version 0.0.4) metrics rendering.
  2. Source watchdog health monitoring and automated alert dispatch.
  3. Ingress adapter health telemetry schemas.
"""

import time
import pytest
from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.prometheus import PrometheusExporter
from mdrap.reconciliation import ReliabilityTracker
from mdrap.watchdog import SourceState, SourceWatchdog, WatchdogAlert


def test_prometheus_metrics_export_formatting():
    """PrometheusExporter renders standard Prometheus 0.0.4 text exposition with required headers."""
    exporter = PrometheusExporter()

    # Record API requests and durations
    exporter.record_api_request("GET", "/health", 0.0012, 200)
    exporter.record_api_request("POST", "/v1/events", 0.0045, 200)
    exporter.record_api_request("POST", "/v1/events", 0.0008, 403)

    # Set custom metrics
    exporter.inc_counter("dropped", 5.0)

    # Render metrics text
    rendered = exporter.render()
    assert isinstance(rendered, str)
    assert "# HELP" in rendered
    assert "# TYPE" in rendered

    # Verify standard pipeline & API metrics
    assert "mdrap_events_dropped_total 5" in rendered
    assert 'mdrap_api_requests_total{method="GET",endpoint="/health"} 1' in rendered
    assert 'mdrap_api_requests_total{method="POST",endpoint="/v1/events"} 2' in rendered
    assert 'mdrap_api_errors_total{method="POST",endpoint="/v1/events"} 1' in rendered


def test_source_watchdog_alerting():
    """SourceWatchdog tracks feed silence and dispatches WatchdogAlert notifications."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(
        reliability=tracker,
        silence_threshold_s=2.0,
    )

    # Observe initial events from NASDAQ and ARCA at time 100.0
    e_nasdaq = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.0,
        processing_timestamp=100.0,
        source="NASDAQ",
        sequence_number=1,
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )
    e_arca_1 = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.0,
        processing_timestamp=100.0,
        source="ARCA",
        sequence_number=1,
        price=150.02,
        quantity=50.0,
        quality_status=QualityStatus.VALID,
    )

    watchdog.observe(e_nasdaq)
    watchdog.observe(e_arca_1)
    assert watchdog.source_states()["NASDAQ"] == SourceState.HEALTHY
    assert watchdog.source_states()["ARCA"] == SourceState.HEALTHY

    # Advance market time to 105.0 on ARCA while NASDAQ remains silent
    e_arca_2 = CanonicalEvent(
        event_id="e3",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=105.0,
        receive_timestamp=105.0,
        processing_timestamp=105.0,
        source="ARCA",
        sequence_number=2,
        price=150.05,
        quantity=50.0,
        quality_status=QualityStatus.VALID,
    )

    alerts = watchdog.observe(e_arca_2)
    assert len(alerts) >= 1
    silence_alerts = [a for a in alerts if a.alert_type == "SILENCE"]
    assert len(silence_alerts) == 1
    assert silence_alerts[0].source == "NASDAQ"
    assert watchdog.source_states()["NASDAQ"] == SourceState.SILENT


def test_ingress_health_telemetry():
    """Ingress adapters expose comprehensive, well-structured telemetry for operational dashboards."""
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="NYSE", feed_id="ORDER_IMBALANCE_1"),
        frames=[{"seq": 1, "sym": "IBM", "px": 140.0, "sz": 50.0, "type": "TRADE"}],
    )
    adapter.connect()
    adapter.subscribe("IBM")
    adapter.subscribe("AAPL")

    health = adapter.health()
    assert health["venue"] == "NYSE"
    assert health["feed_id"] == "ORDER_IMBALANCE_1"
    assert health["state"] == "CONNECTED"
    assert health["connected"] is True
    assert set(health["subscribed_symbols"]) == {"IBM", "AAPL"}
    assert health["gaps_detected"] == 0

    adapter.disconnect()
    health_closed = adapter.health()
    assert health_closed["state"] == "CLOSED"
    assert health_closed["connected"] is False
