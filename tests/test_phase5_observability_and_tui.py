"""Acceptance test suite for Phase 5: Observability, Operator Experience & Terminal UI.

Verifies:
1. OpenTelemetry Metrics Tracking:
   - Standard counters: mdrap_events_received_total, mdrap_events_quarantined_total, mdrap_drops_total.
   - Latency histograms: mdrap_wire_latency_seconds, mdrap_engine_latency_seconds, mdrap_fsync_latency_seconds.
2. Strict Bounded Cardinality:
   - High-cardinality protection: unapproved venues, reasons, and causes map to 'OTHER'.
   - Zero metric label explosion in hot paths.
3. Dual Metrics Export Formats:
   - Prometheus text exposition format (version 0.0.4).
   - OpenTelemetry OTLP JSON (v1 ResourceMetrics).
4. Terminal Visual Dashboard (TUI):
   - Sparkline generation with Unicode and ASCII fallback.
   - Microsecond latency percentiles (p50, p90, p95, p99, p99.9).
   - Real-time bid/ask order book depth ladder with bar scaling.
   - Forensic quarantine alert ticker.
   - Headless string rendering support for automation and testing.
5. Unified CLI Integration:
   - mdrap dashboard --render-once
   - mdrap metrics --format prometheus / otlp / json
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.telemetry import (
    ALLOWED_CAUSES,
    ALLOWED_EVENT_TYPES,
    ALLOWED_REASONS,
    ALLOWED_VENUES,
    TelemetryRegistry,
    get_telemetry,
)
from mdrap.tui import TerminalDashboard, calc_percentile, render_sparkline


class TestOpenTelemetryMetricsTracking:
    """Verifies counter and latency histogram metrics recording."""

    def test_counters_recording(self):
        reg = TelemetryRegistry()
        reg.record_received(venue="BINANCE", event_type="TRADE", count=100)
        reg.record_received(venue="KRAKEN", event_type="QUOTE", count=50)

        reg.record_quarantined(venue="BINANCE", reason="PRICE_OUTLIER", count=3)
        reg.record_quarantined(venue="KRAKEN", reason="CROSSED_QUOTE", count=2)

        reg.record_drop(venue="BINANCE", cause="BACKPRESSURE", count=5)
        reg.record_drop(venue="KRAKEN", cause="QUEUE_FULL", count=1)

        summary = reg.summary()
        assert summary["events_received_total"] == 150
        assert summary["events_quarantined_total"] == 5
        assert summary["drops_total"] == 6

        assert summary["events_received"]["BINANCE:TRADE"] == 100
        assert summary["events_received"]["KRAKEN:QUOTE"] == 50
        assert summary["events_quarantined"]["BINANCE:PRICE_OUTLIER"] == 3
        assert summary["drops"]["BINANCE:BACKPRESSURE"] == 5

    def test_latency_histograms(self):
        reg = TelemetryRegistry()

        # Wire latency: 50 microseconds
        reg.record_wire_latency(0.000050, venue="BINANCE")
        # Engine latency: 2.5 microseconds
        reg.record_engine_latency(0.0000025, venue="BINANCE")
        # Fsync latency: 2 milliseconds
        reg.record_fsync_latency(0.002000)

        summary = reg.summary()
        assert summary["events_received_total"] == 0

        prom = reg.export_prometheus()
        assert "mdrap_wire_latency_seconds_bucket" in prom
        assert "mdrap_engine_latency_seconds_bucket" in prom
        assert "mdrap_fsync_latency_seconds_bucket" in prom
        assert 'venue="BINANCE"' in prom


class TestStrictBoundedCardinality:
    """Verifies that arbitrary external strings cannot cause label explosion."""

    def test_unapproved_venue_sanitized_to_other(self):
        reg = TelemetryRegistry()
        # Feed 100 distinct malicious/arbitrary venues
        for i in range(100):
            reg.record_received(venue=f"MALICIOUS_INJECTED_VENUE_{i}", event_type="TRADE", count=1)

        summary = reg.summary()
        assert summary["events_received_total"] == 100
        # All 100 distinct names must collapse to the single bounded label "OTHER"
        assert len(summary["events_received"]) == 1
        assert "OTHER:TRADE" in summary["events_received"]
        assert summary["events_received"]["OTHER:TRADE"] == 100

    def test_unapproved_reason_sanitized_to_other(self):
        reg = TelemetryRegistry()
        for i in range(50):
            reg.record_quarantined(venue="SIM", reason=f"RANDOM_ERROR_STRING_{i}", count=2)

        summary = reg.summary()
        assert summary["events_quarantined_total"] == 100
        assert len(summary["events_quarantined"]) == 1
        assert "SIM:OTHER" in summary["events_quarantined"]
        assert summary["events_quarantined"]["SIM:OTHER"] == 100

    def test_unapproved_cause_sanitized_to_other(self):
        reg = TelemetryRegistry()
        reg.record_drop(venue="CME", cause="UNKNOWN_BUFFER_CRASH_XYZ", count=10)
        summary = reg.summary()
        assert "CME:OTHER" in summary["drops"]


class TestDualExportFormats:
    """Verifies standard Prometheus 0.0.4 text and OpenTelemetry OTLP JSON exports."""

    def test_prometheus_exposition_format(self):
        reg = TelemetryRegistry()
        reg.record_received(venue="BINANCE", event_type="TRADE", count=42)
        reg.record_quarantined(venue="BINANCE", reason="PRICE_STALE", count=2)
        reg.record_wire_latency(0.000100, venue="BINANCE")
        reg.set_gauge("mdrap_active_streams", 3)

        prom_text = reg.export_prometheus()
        assert isinstance(prom_text, str)
        assert "# HELP mdrap_events_received_total" in prom_text
        assert "# TYPE mdrap_events_received_total counter" in prom_text
        assert 'mdrap_events_received_total{venue="BINANCE",event_type="TRADE"} 42' in prom_text
        assert 'mdrap_events_quarantined_total{venue="BINANCE",reason="PRICE_STALE"} 2' in prom_text
        assert "mdrap_wire_latency_seconds_count{venue=\"BINANCE\"} 1" in prom_text
        assert "mdrap_active_streams 3.0" in prom_text

    def test_otlp_json_format(self):
        reg = TelemetryRegistry()
        reg.record_received(venue="DATABENTO", event_type="TRADE", count=1000)
        reg.record_quarantined(venue="DATABENTO", reason="CROSSED_QUOTE", count=5)
        reg.record_engine_latency(0.000005, venue="DATABENTO")

        otlp = reg.export_otlp_json()
        assert isinstance(otlp, dict)
        assert "resourceMetrics" in otlp
        rm = otlp["resourceMetrics"][0]
        assert rm["resource"]["attributes"][0]["value"]["stringValue"] == "mdrap"

        scope_metrics = rm["scopeMetrics"][0]
        metric_names = [m["name"] for m in scope_metrics["metrics"]]
        assert "mdrap_events_received_total" in metric_names
        assert "mdrap_events_quarantined_total" in metric_names
        assert "mdrap_engine_latency_seconds" in metric_names


class TestTerminalVisualDashboard:
    """Verifies TUI sparkline rendering, latency percentiles, and depth ladder."""

    def test_sparkline_rendering(self):
        values = [10.0, 11.0, 12.0, 15.0, 20.0, 18.0, 14.0, 10.0]
        spark = render_sparkline(values, width=8)
        assert len(spark) == 8
        # Flat values produce constant middle character
        flat_spark = render_sparkline([50.0] * 10, width=10)
        assert len(flat_spark) == 10

    def test_percentile_calculation(self):
        data = sorted([float(x) for x in range(1, 101)])  # 1 to 100
        assert calc_percentile(data, 0.50) == pytest.approx(50.5, abs=0.5)
        assert calc_percentile(data, 0.90) == pytest.approx(90.1, abs=0.5)
        assert calc_percentile(data, 0.99) == pytest.approx(99.0, abs=0.5)

    def test_headless_dashboard_render_string(self):
        dash = TerminalDashboard(symbol="BTC/USDT", target="sim:BTC/USDT")

        # Ingest 25 valid ticks
        for i in range(25):
            tick = CanonicalEvent(
                event_id=f"tick_{i}",
                instrument_id="BTC/USDT",
                event_type=EventType.TRADE,
                exchange_timestamp=1729000000.0 + i,
                receive_timestamp=1729000000.0 + i,
                processing_timestamp=time.time(),
                source="SIM",
                price=65000.0 + (i * 0.5),
                quantity=1.0,
                bid_price=64999.8,
                ask_price=65000.2,
                quality_status=QualityStatus.VALID,
                sequence_number=i + 1,
            )
            dash._record_tick(tick)

        # Ingest 1 suspicious and 1 invalid tick with reasons
        susp_tick = CanonicalEvent(
            event_id="tick_susp",
            instrument_id="BTC/USDT",
            event_type=EventType.TRADE,
            exchange_timestamp=1729000026.0,
            receive_timestamp=1729000026.0,
            processing_timestamp=time.time(),
            source="SIM",
            price=66000.0,
            quantity=0.1,
            quality_status=QualityStatus.SUSPICIOUS,
            reasons=["PRICE_OUTLIER"],
            sequence_number=27,
        )
        dash._record_tick(susp_tick)

        inv_tick = CanonicalEvent(
            event_id="tick_inv",
            instrument_id="BTC/USDT",
            event_type=EventType.QUOTE,
            exchange_timestamp=1729000027.0,
            receive_timestamp=1729000027.0,
            processing_timestamp=time.time(),
            source="SIM",
            bid_price=65100.0,
            ask_price=65000.0,  # Crossed quote!
            quality_status=QualityStatus.INVALID,
            reasons=["CROSSED_QUOTE"],
            sequence_number=28,
        )
        dash._record_tick(inv_tick)

        # Render string output
        output = dash.render_frame_to_string(inv_tick)
        assert isinstance(output, str)
        assert "MDRAP LIVE DASHBOARD | BTC/USDT" in output
        assert "Last Price" in output
        assert "Sparkline:" in output
        assert "Quality    : INVALID" in output
        assert "Valid: 25" in output
        assert "Suspicious: 1" in output
        assert "Invalid: 1" in output
        assert "Latency us : p50:" in output
        assert "REAL-TIME ORDER BOOK LADDER:" in output
        assert "ASK" in output
        assert "BID" in output
        assert "FORENSIC QUARANTINE & ANOMALY ALERTS:" in output
        assert "CROSSED_QUOTE" in output


class TestCLIObservabilityCommands:
    """Verifies unified CLI commands: mdrap dashboard, mdrap metrics."""

    def test_cli_dashboard_render_once(self):
        cmd = [sys.executable, "cli.py", "tui", "ETHUSDT", "--render-once"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        assert "MDRAP LIVE DASHBOARD | ETHUSDT" in res.stdout
        assert "REAL-TIME ORDER BOOK LADDER" in res.stdout
        assert "Latency us" in res.stdout

    def test_cli_metrics_prometheus_format(self):
        cmd = [sys.executable, "cli.py", "metrics", "--format", "prometheus"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        assert "# HELP mdrap_events_received_total" in res.stdout
        assert "# TYPE mdrap_events_received_total counter" in res.stdout

    def test_cli_metrics_otlp_format(self):
        cmd = [sys.executable, "cli.py", "metrics", "--format", "otlp"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert "resourceMetrics" in data

    def test_cli_metrics_json_summary_format(self):
        cmd = [sys.executable, "cli.py", "metrics", "--format", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert "uptime_seconds" in data
        assert "events_received_total" in data
