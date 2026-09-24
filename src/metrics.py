"""
Observability: throughput, tail latency, dropped/invalid counters,
resource usage. Kept dependency-light (stdlib only) so it never
becomes the bottleneck it's supposed to be measuring.
"""

from __future__ import annotations

import array
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import resource  # POSIX only, fine for this sandbox
except ImportError:  # pragma: no cover
    resource = None

__stability__ = "stable"

# Bound on the "recent" window used for *live* dashboard percentiles.
# Sorting this is O(bound log bound) on every refresh instead of
# O(total_events log total_events) -- see RunMetrics docstring.
LIVE_WINDOW = 5000


class CompactSampleBuffer:
    """Memory-efficient downsampled array buffer using 32-bit floats.

    Replaces unbounded Python list[float] (32 bytes per entry) with array('f')
    (4 bytes per entry) and systematic 2x downsampling when capacity is reached.
    Guarantees O(1) space (< 200 KB) for any number of events (100k to 1B)
    while preserving temporal representation and exact max value.
    """

    __slots__ = ("capacity", "data", "stride", "_counter", "max_val")

    def __init__(self, capacity: int = 50_000):
        self.capacity = capacity
        self.data = array.array("f")
        self.stride = 1
        self._counter = 0
        self.max_val = 0.0

    def append(self, val: float) -> None:
        val_f = float(val)
        if val_f > self.max_val:
            self.max_val = val_f
        self._counter += 1
        if self._counter % self.stride == 0:
            self.data.append(val_f)
            if len(self.data) >= self.capacity:
                self.data = self.data[::2]
                self.stride *= 2

    def __len__(self) -> int:
        return len(self.data)

    def __bool__(self) -> bool:
        return len(self.data) > 0

    def __iter__(self):
        return iter(self.data)

    def sorted(self) -> list[float]:
        return sorted(self.data)


def percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (len(sorted_values) - 1) * pct
    f, c = int(idx), min(int(idx) + 1, len(sorted_values) - 1)
    return (
        sorted_values[f]
        if f == c
        else sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (idx - f)
    )


def get_rss_mb() -> float:
    """Return process Resident Set Size (RSS) in MB without external dependencies."""
    if resource:
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return (
            rss_kb / 1024.0 if sys.platform == "linux" else rss_kb / (1024.0 * 1024.0)
        )
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            h_process = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(h_process, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / (1024.0 * 1024.0)
        except Exception:
            pass
    return 0.0


@dataclass
class RunMetrics:
    """Full latency history (`_latencies_us`/`_proc_latencies_us`) is kept
    for an accurate final `summary()` -- that's a one-time O(n log n) sort
    at the end of a run, which is fine. A live dashboard calling that same
    sort on every refresh is NOT fine: it turns an O(n log n) one-off cost
    into an O(events/refresh_interval * n log n) cost that grows with the
    run. `recent_latencies_us`/`recent_proc_latencies_us` are small bounded
    deques (see LIVE_WINDOW) that dashboard.py reads instead, so live
    percentiles stay cheap regardless of how long the run has been going.
    """

    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    processed: int = 0
    dropped: int = 0  # events that raised an unrecoverable error (rare; schema failures are INVALID, not dropped)
    quality_counts: Dict[str, int] = field(
        default_factory=lambda: {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
    )
    _latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(50_000)
    )  # exchange -> canonical-decision latency
    _proc_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(50_000)
    )  # ingest -> canonical-decision compute time
    recent_latencies_us: deque = field(
        default_factory=lambda: deque(maxlen=LIVE_WINDOW)
    )
    recent_proc_latencies_us: deque = field(
        default_factory=lambda: deque(maxlen=LIVE_WINDOW)
    )
    max_queue_depth: int = 0
    backpressure_stalls: int = 0
    _queue_depths: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(10_000)
    )
    _storage_lags_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(10_000)
    )
    _ingest_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(20_000)
    )
    _quality_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(20_000)
    )
    _reconcile_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(20_000)
    )
    _enqueue_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(20_000)
    )
    _query_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(10_000)
    )
    recent_query_latencies_us: deque = field(
        default_factory=lambda: deque(maxlen=LIVE_WINDOW)
    )
    _flush_latencies_us: CompactSampleBuffer = field(
        default_factory=lambda: CompactSampleBuffer(10_000)
    )
    _by_source_us: Dict[str, CompactSampleBuffer] = field(default_factory=dict)
    _by_instrument_us: Dict[str, CompactSampleBuffer] = field(default_factory=dict)

    def record(
        self,
        e2e_latency_s: float,
        processing_latency_s: float,
        status: str,
        source: str | None = None,
        instrument_id: str | None = None,
        ingest_latency_s: float = 0.0,
        quality_latency_s: float = 0.0,
        reconcile_latency_s: float = 0.0,
        enqueue_latency_s: float = 0.0,
    ):
        self.processed += 1
        self.quality_counts[status] = self.quality_counts.get(status, 0) + 1
        e2e_us = e2e_latency_s * 1_000_000
        proc_us = processing_latency_s * 1_000_000
        self._latencies_us.append(e2e_us)
        self._proc_latencies_us.append(proc_us)
        self.recent_latencies_us.append(e2e_us)
        self.recent_proc_latencies_us.append(proc_us)

        if ingest_latency_s > 0.0:
            self._ingest_latencies_us.append(ingest_latency_s * 1_000_000)
        if quality_latency_s > 0.0:
            self._quality_latencies_us.append(quality_latency_s * 1_000_000)
        if reconcile_latency_s > 0.0:
            self._reconcile_latencies_us.append(reconcile_latency_s * 1_000_000)
        if enqueue_latency_s > 0.0:
            self._enqueue_latencies_us.append(enqueue_latency_s * 1_000_000)

        if source:
            sb = self._by_source_us.get(source)
            if sb is None:
                sb = self._by_source_us[source] = CompactSampleBuffer(10_000)
            sb.append(proc_us)

        if instrument_id:
            ib = self._by_instrument_us.get(instrument_id)
            if ib is None:
                ib = self._by_instrument_us[instrument_id] = CompactSampleBuffer(10_000)
            ib.append(proc_us)

    def record_query(self, query_latency_s: float):
        """Record point or analytical query latency in microseconds."""
        q_us = query_latency_s * 1_000_000
        self._query_latencies_us.append(q_us)
        self.recent_query_latencies_us.append(q_us)

    def record_streaming(
        self,
        queue_depth: int,
        backpressure_stall: bool = False,
        storage_lag_s: float = 0.0,
    ):
        if queue_depth > self.max_queue_depth:
            self.max_queue_depth = queue_depth
        self._queue_depths.append(queue_depth)
        if backpressure_stall:
            self.backpressure_stalls += 1
        if storage_lag_s > 0.0:
            self._storage_lags_us.append(storage_lag_s * 1_000_000)

    def record_flush(self, flush_duration_s: float):
        """Record storage batch commit / WAL flush duration in microseconds."""
        if flush_duration_s > 0.0:
            self._flush_latencies_us.append(flush_duration_s * 1_000_000)

    def finish(self):
        self.end_time = time.time()

    def elapsed_s(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def throughput(self) -> float:
        el = self.elapsed_s()
        return self.processed / el if el > 0 else 0.0

    def summary(self) -> dict:
        lat = (
            self._latencies_us.sorted()
            if isinstance(self._latencies_us, CompactSampleBuffer)
            else sorted(self._latencies_us)
        )
        proc = (
            self._proc_latencies_us.sorted()
            if isinstance(self._proc_latencies_us, CompactSampleBuffer)
            else sorted(self._proc_latencies_us)
        )
        rss_mb = get_rss_mb()

        e2e_p50 = percentile(lat, 0.50)
        proc_p50 = percentile(proc, 0.50)
        max_e2e = round(
            self._latencies_us.max_val
            if isinstance(self._latencies_us, CompactSampleBuffer)
            else (lat[-1] if lat else 0.0),
            1,
        )
        max_proc = round(
            self._proc_latencies_us.max_val
            if isinstance(self._proc_latencies_us, CompactSampleBuffer)
            else (proc[-1] if proc else 0.0),
            1,
        )

        result = {
            "processed": self.processed,
            "dropped": self.dropped,
            "elapsed_s": round(self.elapsed_s(), 4),
            "throughput_eps": round(self.throughput(), 1),
            "quality_counts": self.quality_counts,
            "e2e_latency_us": {
                "p50": round(e2e_p50, 1),
                "p95": round(percentile(lat, 0.95), 1),
                "p99": round(percentile(lat, 0.99), 1),
                "p999": round(percentile(lat, 0.999), 1),
                "max": max_e2e,
            },
            "processing_latency_us": {
                "p50": round(proc_p50, 1),
                "p95": round(percentile(proc, 0.95), 1),
                "p99": round(percentile(proc, 0.99), 1),
                "max": max_proc,
            },
            "processing_latency_ns": {
                "p50": int(proc_p50 * 1000),
                "p95": int(percentile(proc, 0.95) * 1000),
                "p99": int(percentile(proc, 0.99) * 1000),
                "max": int(max_proc * 1000),
            },
            "latency_split_e2e_vs_proc": {
                "e2e_p50_us": round(e2e_p50, 1),
                "proc_p50_us": round(proc_p50, 1),
                "e2e_to_proc_ratio": round(e2e_p50 / max(0.001, proc_p50), 2)
                if proc_p50
                else 1.0,
            },
            "max_rss_mb": round(rss_mb, 1) if rss_mb else None,
        }

        # Stage breakdowns (Phase 0)
        def _calc_stage(vals: Any) -> dict:
            if not vals:
                return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
            s = vals.sorted() if isinstance(vals, CompactSampleBuffer) else sorted(vals)
            return {
                "p50": round(percentile(s, 0.50), 2),
                "p95": round(percentile(s, 0.95), 2),
                "p99": round(percentile(s, 0.99), 2),
            }

        if any(
            [
                self._ingest_latencies_us,
                self._quality_latencies_us,
                self._reconcile_latencies_us,
                self._enqueue_latencies_us,
                self._flush_latencies_us,
            ]
        ):
            result["stages_us"] = {
                "ingest_normalize": _calc_stage(self._ingest_latencies_us),
                "quality_evaluate": _calc_stage(self._quality_latencies_us),
                "reconcile_analytics": _calc_stage(self._reconcile_latencies_us),
                "enqueue_storage": _calc_stage(self._enqueue_latencies_us),
                "wal_storage_flush": _calc_stage(self._flush_latencies_us),
            }

        # Per-source breakdown (Q2)
        if self._by_source_us:
            result["by_source_proc_us"] = {
                src: _calc_stage(vals)
                for src, vals in sorted(self._by_source_us.items())
            }

        # Per-instrument breakdown (top 10 by volume) (Q2)
        if self._by_instrument_us:
            sorted_insts = sorted(
                self._by_instrument_us.items(), key=lambda kv: len(kv[1]), reverse=True
            )[:10]
            result["by_instrument_proc_us"] = {
                inst: _calc_stage(vals) for inst, vals in sorted_insts
            }

        # Query latency breakdown
        if self._query_latencies_us:
            q_sorted = (
                self._query_latencies_us.sorted()
                if isinstance(self._query_latencies_us, CompactSampleBuffer)
                else sorted(self._query_latencies_us)
            )
            max_q = round(
                self._query_latencies_us.max_val
                if isinstance(self._query_latencies_us, CompactSampleBuffer)
                else (q_sorted[-1] if q_sorted else 0.0),
                2,
            )
            result["query_latency_us"] = {
                "count": len(self._query_latencies_us),
                "p50": round(percentile(q_sorted, 0.50), 2),
                "p95": round(percentile(q_sorted, 0.95), 2),
                "p99": round(percentile(q_sorted, 0.99), 2),
                "max": max_q,
            }

        if self._queue_depths:
            q_sorted = (
                self._queue_depths.sorted()
                if isinstance(self._queue_depths, CompactSampleBuffer)
                else sorted(self._queue_depths)
            )
            result["streaming"] = {
                "max_queue_depth": self.max_queue_depth,
                "p50_queue_depth": round(percentile(q_sorted, 0.50), 1),
                "p95_queue_depth": round(percentile(q_sorted, 0.95), 1),
                "backpressure_stalls": self.backpressure_stalls,
            }
            if self._storage_lags_us:
                sl_sorted = (
                    self._storage_lags_us.sorted()
                    if isinstance(self._storage_lags_us, CompactSampleBuffer)
                    else sorted(self._storage_lags_us)
                )
                max_sl = round(
                    self._storage_lags_us.max_val
                    if isinstance(self._storage_lags_us, CompactSampleBuffer)
                    else (sl_sorted[-1] if sl_sorted else 0.0),
                    1,
                )
                result["streaming"]["storage_lag_us"] = {
                    "p50": round(percentile(sl_sorted, 0.50), 1),
                    "p95": round(percentile(sl_sorted, 0.95), 1),
                    "max": max_sl,
                }

        return result
