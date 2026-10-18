"""OpenTelemetry Metrics Exporter & Bounded-Cardinality Telemetry for MDRAP (Phase 5).

Provides production-grade operational observability with:
- Standard OpenTelemetry counters:
    - mdrap_events_received_total{venue, event_type}
    - mdrap_events_quarantined_total{venue, reason}
    - mdrap_drops_total{venue, cause}
- Latency histograms with sub-microsecond buckets:
    - mdrap_wire_latency_seconds
    - mdrap_engine_latency_seconds
    - mdrap_fsync_latency_seconds
- Strict bounded cardinality:
    - Guarantees zero time-series explosion in hot paths by mapping unapproved labels to 'OTHER'.
- Dual export capability:
    - OTLP JSON (v1 metrics payload for OpenTelemetry Collector)
    - Prometheus text exposition format (version 0.0.4)
"""

from __future__ import annotations

__stability__ = "stable"

import bisect
import collections
from dataclasses import dataclass, field
import json
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ._version import __version__

logger = logging.getLogger("mdrap.telemetry")

# Strict cardinality sets to avoid hot-path label explosion (Phase 5)
ALLOWED_VENUES: Set[str] = {
    "BINANCE",
    "KRAKEN",
    "COINBASE",
    "DATABENTO",
    "NASDAQ_ITCH",
    "SIM",
    "CME",
    "EUREX",
    "POLYGON",
    "OKX",
    "BYBIT",
    "SHM",
    "FILE",
    "WAL",
    "OTHER",
}

ALLOWED_EVENT_TYPES: Set[str] = {"TRADE", "QUOTE", "DEPTH", "STATUS", "OTHER"}

ALLOWED_REASONS: Set[str] = {
    "PRICE_NEGATIVE_OR_ZERO",
    "SIZE_NON_POSITIVE",
    "PRICE_STALE",
    "CROSSED_QUOTE",
    "LOCKED_MARKET",
    "PRICE_OUTLIER",
    "PRICE_WINDOW_EMPTY",
    "SCHEMA_VIOLATION",
    "SEQUENCE_GAP",
    "DUPLICATE_SEQUENCE",
    "CROSS_FEED_DISAGREEMENT",
    "CLOCK_SKEW_FUTURE",
    "ANOMALOUS_SIZE",
    "OTHER",
}

ALLOWED_CAUSES: Set[str] = {
    "BACKPRESSURE",
    "QUEUE_FULL",
    "DESERIALIZE_ERROR",
    "TIMEOUT",
    "DISCONNECT",
    "CORRUPTED_CRC",
    "OTHER",
}

# Standard Latency Histogram Bucket Boundaries (in seconds)
# Wire: 1us to 100ms
BUCKETS_WIRE_LATENCY = (
    0.000001,  # 1 us
    0.000005,  # 5 us
    0.000010,  # 10 us
    0.000050,  # 50 us
    0.000100,  # 100 us
    0.000500,  # 500 us
    0.001000,  # 1 ms
    0.005000,  # 5 ms
    0.010000,  # 10 ms
    0.050000,  # 50 ms
    0.100000,  # 100 ms
)

# Engine: 500ns to 1ms
BUCKETS_ENGINE_LATENCY = (
    0.0000005,  # 500 ns
    0.0000010,  # 1 us
    0.0000025,  # 2.5 us
    0.0000050,  # 5 us
    0.0000100,  # 10 us
    0.0000500,  # 50 us
    0.0001000,  # 100 us
    0.0005000,  # 500 us
    0.0010000,  # 1 ms
)

# Fsync: 100us to 500ms
BUCKETS_FSYNC_LATENCY = (
    0.000100,  # 100 us
    0.000500,  # 500 us
    0.001000,  # 1 ms
    0.005000,  # 5 ms
    0.010000,  # 10 ms
    0.025000,  # 25 ms
    0.050000,  # 50 ms
    0.100000,  # 100 ms
    0.500000,  # 500 ms
)


def _sanitize_label(value: Any, allowed: Set[str]) -> str:
    """Normalize label to uppercase and map unknown high-cardinality values to 'OTHER'."""
    if value is None:
        return "OTHER"
    val_clean = str(value).strip().upper()
    return val_clean if val_clean in allowed else "OTHER"


class Histogram:
    """Thread-safe, bounded-bucket latency histogram."""

    def __init__(self, boundaries: Sequence[float]) -> None:
        self.boundaries = tuple(sorted(boundaries))
        self.counts = [0] * (len(self.boundaries) + 1)
        self.total_count = 0
        self.total_sum = 0.0
        self._lock = threading.Lock()

    def observe(self, val_seconds: float) -> None:
        v = float(val_seconds)
        if not math.isfinite(v) or v < 0:
            v = 0.0
        idx = bisect.bisect_right(self.boundaries, v)
        with self._lock:
            self.counts[idx] += 1
            self.total_count += 1
            self.total_sum += v

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "boundaries": self.boundaries,
                "counts": list(self.counts),
                "total_count": self.total_count,
                "total_sum": self.total_sum,
            }


class TelemetryRegistry:
    """Institutional Metrics Registry with strict cardinality controls and dual export."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.start_time = time.time()

        # 1. Counters: (venue, secondary) -> int
        self._events_received: Dict[Tuple[str, str], int] = collections.defaultdict(int)
        self._events_quarantined: Dict[Tuple[str, str], int] = collections.defaultdict(
            int
        )
        self._drops: Dict[Tuple[str, str], int] = collections.defaultdict(int)

        # 2. Histograms: metric_name -> (labels_tuple -> Histogram)
        self._histograms: Dict[str, Dict[Tuple[str, ...], Histogram]] = {
            "mdrap_wire_latency_seconds": {},
            "mdrap_engine_latency_seconds": {},
            "mdrap_fsync_latency_seconds": {},
        }

        # 3. Gauges
        self._gauges: Dict[str, float] = {}

    def record_received(
        self, venue: str, event_type: str = "TRADE", count: int = 1
    ) -> None:
        """Record received market data event with bounded cardinality."""
        v = _sanitize_label(venue, ALLOWED_VENUES)
        et = _sanitize_label(event_type, ALLOWED_EVENT_TYPES)
        with self._lock:
            self._events_received[(v, et)] += count

    def record_quarantined(
        self, venue: str, reason: str = "PRICE_OUTLIER", count: int = 1
    ) -> None:
        """Record quarantined market data event with bounded cardinality."""
        v = _sanitize_label(venue, ALLOWED_VENUES)
        r = _sanitize_label(reason, ALLOWED_REASONS)
        with self._lock:
            self._events_quarantined[(v, r)] += count

    def record_drop(
        self, venue: str, cause: str = "BACKPRESSURE", count: int = 1
    ) -> None:
        """Record dropped event with bounded cardinality."""
        v = _sanitize_label(venue, ALLOWED_VENUES)
        c = _sanitize_label(cause, ALLOWED_CAUSES)
        with self._lock:
            self._drops[(v, c)] += count

    def record_wire_latency(self, seconds: float, venue: str = "SIM") -> None:
        """Record ingress network wire latency (exchange timestamp to gateway receive)."""
        v = _sanitize_label(venue, ALLOWED_VENUES)
        key = (v,)
        with self._lock:
            if key not in self._histograms["mdrap_wire_latency_seconds"]:
                self._histograms["mdrap_wire_latency_seconds"][key] = Histogram(
                    BUCKETS_WIRE_LATENCY
                )
            hist = self._histograms["mdrap_wire_latency_seconds"][key]
        hist.observe(seconds)

    def record_engine_latency(self, seconds: float, venue: str = "SIM") -> None:
        """Record internal engine processing latency (gateway receive to canonical publish)."""
        v = _sanitize_label(venue, ALLOWED_VENUES)
        key = (v,)
        with self._lock:
            if key not in self._histograms["mdrap_engine_latency_seconds"]:
                self._histograms["mdrap_engine_latency_seconds"][key] = Histogram(
                    BUCKETS_ENGINE_LATENCY
                )
            hist = self._histograms["mdrap_engine_latency_seconds"][key]
        hist.observe(seconds)

    def record_fsync_latency(self, seconds: float) -> None:
        """Record physical non-volatile disk synchronization latency."""
        key: Tuple[str, ...] = ()
        with self._lock:
            if key not in self._histograms["mdrap_fsync_latency_seconds"]:
                self._histograms["mdrap_fsync_latency_seconds"][key] = Histogram(
                    BUCKETS_FSYNC_LATENCY
                )
            hist = self._histograms["mdrap_fsync_latency_seconds"][key]
        hist.observe(seconds)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = float(value)

    def summary(self) -> dict[str, Any]:
        """Return a structured dictionary snapshot of all tracked metrics."""
        with self._lock:
            rec_tot = sum(self._events_received.values())
            quar_tot = sum(self._events_quarantined.values())
            drop_tot = sum(self._drops.values())

            return {
                "uptime_seconds": round(time.time() - self.start_time, 2),
                "events_received_total": rec_tot,
                "events_quarantined_total": quar_tot,
                "drops_total": drop_tot,
                "events_received": {
                    f"{v}:{et}": c for (v, et), c in self._events_received.items()
                },
                "events_quarantined": {
                    f"{v}:{r}": c for (v, r), c in self._events_quarantined.items()
                },
                "drops": {f"{v}:{c}": cnt for (v, c), cnt in self._drops.items()},
                "gauges": dict(self._gauges),
            }

    def export_prometheus(self) -> str:
        """Format metrics as Prometheus text exposition format (version 0.0.4)."""
        lines = []

        # 1. Received counter
        lines.append(
            "# HELP mdrap_events_received_total Total market data events received at gateway boundary."
        )
        lines.append("# TYPE mdrap_events_received_total counter")
        with self._lock:
            for (v, et), count in sorted(self._events_received.items()):
                lines.append(
                    f'mdrap_events_received_total{{venue="{v}",event_type="{et}"}} {count}'
                )

        # 2. Quarantined counter
        lines.append(
            "# HELP mdrap_events_quarantined_total Total market data events quarantined by quality rule engine."
        )
        lines.append("# TYPE mdrap_events_quarantined_total counter")
        with self._lock:
            for (v, r), count in sorted(self._events_quarantined.items()):
                lines.append(
                    f'mdrap_events_quarantined_total{{venue="{v}",reason="{r}"}} {count}'
                )

        # 3. Drops counter
        lines.append(
            "# HELP mdrap_drops_total Total market data frames dropped due to backpressure or queue limits."
        )
        lines.append("# TYPE mdrap_drops_total counter")
        with self._lock:
            for (v, c), count in sorted(self._drops.items()):
                lines.append(f'mdrap_drops_total{{venue="{v}",cause="{c}"}} {count}')

        # 4. Histograms
        hist_defs = [
            (
                "mdrap_wire_latency_seconds",
                "Inbound network wire latency distribution in seconds.",
            ),
            (
                "mdrap_engine_latency_seconds",
                "Internal normalization and quality engine latency distribution in seconds.",
            ),
            (
                "mdrap_fsync_latency_seconds",
                "Non-volatile storage fsync synchronization latency distribution in seconds.",
            ),
        ]

        for m_name, m_help in hist_defs:
            lines.append(f"# HELP {m_name} {m_help}")
            lines.append(f"# TYPE {m_name} histogram")
            with self._lock:
                series_dict = dict(self._histograms.get(m_name, {}))

            for labels_tuple, hist in sorted(series_dict.items()):
                snap = hist.snapshot()
                label_prefix = f'venue="{labels_tuple[0]}",' if labels_tuple else ""
                cumulative = 0
                for b_idx, upper_bound in enumerate(snap["boundaries"]):
                    cumulative += snap["counts"][b_idx]
                    lines.append(
                        f'{m_name}_bucket{{{label_prefix}le="{upper_bound}"}} {cumulative}'
                    )
                cumulative += snap["counts"][-1]
                lines.append(f'{m_name}_bucket{{{label_prefix}le="+Inf"}} {cumulative}')
                lines.append(
                    f"{m_name}_sum{{{label_prefix[:-1]}}} {snap['total_sum']:.9f}"
                )
                lines.append(
                    f"{m_name}_count{{{label_prefix[:-1]}}} {snap['total_count']}"
                )

        # 5. Gauges
        with self._lock:
            for g_name, g_val in sorted(self._gauges.items()):
                lines.append(f"# TYPE {g_name} gauge")
                lines.append(f"{g_name} {g_val}")

        return "\n".join(lines) + "\n"

    def export_otlp_json(self) -> dict[str, Any]:
        """Export metrics formatted as standard OpenTelemetry OTLP JSON payload."""
        now_nano = int(time.time() * 1e9)
        start_nano = int(self.start_time * 1e9)

        metrics_list = []

        # 1. Received counter
        rec_datapoints = []
        with self._lock:
            for (v, et), val in self._events_received.items():
                rec_datapoints.append(
                    {
                        "attributes": [
                            {"key": "venue", "value": {"stringValue": v}},
                            {"key": "event_type", "value": {"stringValue": et}},
                        ],
                        "startTimeUnixNano": str(start_nano),
                        "timeUnixNano": str(now_nano),
                        "asInt": str(val),
                    }
                )
        if rec_datapoints:
            metrics_list.append(
                {
                    "name": "mdrap_events_received_total",
                    "description": "Total market data events received at gateway boundary",
                    "unit": "1",
                    "sum": {
                        "dataPoints": rec_datapoints,
                        "aggregationTemporality": 2,  # AGGREGATION_TEMPORALITY_CUMULATIVE
                        "isMonotonic": True,
                    },
                }
            )

        # 2. Quarantined counter
        quar_datapoints = []
        with self._lock:
            for (v, r), val in self._events_quarantined.items():
                quar_datapoints.append(
                    {
                        "attributes": [
                            {"key": "venue", "value": {"stringValue": v}},
                            {"key": "reason", "value": {"stringValue": r}},
                        ],
                        "startTimeUnixNano": str(start_nano),
                        "timeUnixNano": str(now_nano),
                        "asInt": str(val),
                    }
                )
        if quar_datapoints:
            metrics_list.append(
                {
                    "name": "mdrap_events_quarantined_total",
                    "description": "Total market data events quarantined by quality engine",
                    "unit": "1",
                    "sum": {
                        "dataPoints": quar_datapoints,
                        "aggregationTemporality": 2,
                        "isMonotonic": True,
                    },
                }
            )

        # 3. Drops counter
        drop_datapoints = []
        with self._lock:
            for (v, c), val in self._drops.items():
                drop_datapoints.append(
                    {
                        "attributes": [
                            {"key": "venue", "value": {"stringValue": v}},
                            {"key": "cause", "value": {"stringValue": c}},
                        ],
                        "startTimeUnixNano": str(start_nano),
                        "timeUnixNano": str(now_nano),
                        "asInt": str(val),
                    }
                )
        if drop_datapoints:
            metrics_list.append(
                {
                    "name": "mdrap_drops_total",
                    "description": "Total market data frames dropped due to backpressure",
                    "unit": "1",
                    "sum": {
                        "dataPoints": drop_datapoints,
                        "aggregationTemporality": 2,
                        "isMonotonic": True,
                    },
                }
            )

        # 4. Histograms
        for m_name in (
            "mdrap_wire_latency_seconds",
            "mdrap_engine_latency_seconds",
            "mdrap_fsync_latency_seconds",
        ):
            hist_dps = []
            with self._lock:
                series_map = dict(self._histograms.get(m_name, {}))

            for labels_tuple, hist in series_map.items():
                snap = hist.snapshot()
                attrs = (
                    [{"key": "venue", "value": {"stringValue": labels_tuple[0]}}]
                    if labels_tuple
                    else []
                )
                hist_dps.append(
                    {
                        "attributes": attrs,
                        "startTimeUnixNano": str(start_nano),
                        "timeUnixNano": str(now_nano),
                        "count": str(snap["total_count"]),
                        "sum": snap["total_sum"],
                        "bucketCounts": [str(c) for c in snap["counts"]],
                        "explicitBounds": list(snap["boundaries"]),
                    }
                )

            if hist_dps:
                metrics_list.append(
                    {
                        "name": m_name,
                        "description": f"Latency distribution for {m_name}",
                        "unit": "s",
                        "histogram": {
                            "dataPoints": hist_dps,
                            "aggregationTemporality": 2,
                        },
                    }
                )

        return {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "mdrap"}},
                            {
                                "key": "service.version",
                                "value": {"stringValue": __version__},
                            },
                        ]
                    },
                    "scopeMetrics": [
                        {
                            "scope": {
                                "name": "mdrap.telemetry",
                                "version": __version__,
                            },
                            "metrics": metrics_list,
                        }
                    ],
                }
            ]
        }


# Global singleton instance
_GLOBAL_REGISTRY: Optional[TelemetryRegistry] = None
_GLOBAL_LOCK = threading.Lock()


def get_telemetry() -> TelemetryRegistry:
    """Access the global singleton telemetry registry."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_REGISTRY is None:
                _GLOBAL_REGISTRY = TelemetryRegistry()
    return _GLOBAL_REGISTRY
