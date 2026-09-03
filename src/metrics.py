"""
Observability: throughput, tail latency, dropped/invalid counters,
resource usage. Kept dependency-light (stdlib only) so it never
becomes the bottleneck it's supposed to be measuring.
"""
from __future__ import annotations

import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import resource  # POSIX only, fine for this sandbox
except ImportError:  # pragma: no cover
    resource = None

# Bound on the "recent" window used for *live* dashboard percentiles.
# Sorting this is O(bound log bound) on every refresh instead of
# O(total_events log total_events) -- see RunMetrics docstring.
LIVE_WINDOW = 5000


def percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


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
    quality_counts: Dict[str, int] = field(default_factory=lambda: {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0})
    _latencies_us: List[float] = field(default_factory=list)  # exchange -> canonical-decision latency
    _proc_latencies_us: List[float] = field(default_factory=list)  # ingest -> canonical-decision compute time
    recent_latencies_us: deque = field(default_factory=lambda: deque(maxlen=LIVE_WINDOW))
    recent_proc_latencies_us: deque = field(default_factory=lambda: deque(maxlen=LIVE_WINDOW))
    max_queue_depth: int = 0
    backpressure_stalls: int = 0
    _queue_depths: List[int] = field(default_factory=list)
    _storage_lags_us: List[float] = field(default_factory=list)

    def record(self, e2e_latency_s: float, processing_latency_s: float, status: str):
        self.processed += 1
        self.quality_counts[status] = self.quality_counts.get(status, 0) + 1
        e2e_us = e2e_latency_s * 1_000_000
        proc_us = processing_latency_s * 1_000_000
        self._latencies_us.append(e2e_us)
        self._proc_latencies_us.append(proc_us)
        self.recent_latencies_us.append(e2e_us)
        self.recent_proc_latencies_us.append(proc_us)

    def record_streaming(self, queue_depth: int, backpressure_stall: bool = False, storage_lag_s: float = 0.0):
        if queue_depth > self.max_queue_depth:
            self.max_queue_depth = queue_depth
        self._queue_depths.append(queue_depth)
        if backpressure_stall:
            self.backpressure_stalls += 1
        if storage_lag_s > 0.0:
            self._storage_lags_us.append(storage_lag_s * 1_000_000)

    def finish(self):
        self.end_time = time.time()

    def elapsed_s(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def throughput(self) -> float:
        el = self.elapsed_s()
        return self.processed / el if el > 0 else 0.0

    def summary(self) -> dict:
        lat = sorted(self._latencies_us)
        proc = sorted(self._proc_latencies_us)
        rss_mb = None
        if resource:
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_mb = rss_kb / 1024 if sys.platform == "linux" else rss_kb / (1024 * 1024)
        elif sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ('cb', wintypes.DWORD),
                        ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t),
                        ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t),
                    ]
                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                    rss_mb = pmc.PeakWorkingSetSize / (1024 * 1024)
            except Exception:
                pass

        result = {
            "processed": self.processed,
            "dropped": self.dropped,
            "elapsed_s": round(self.elapsed_s(), 4),
            "throughput_eps": round(self.throughput(), 1),
            "quality_counts": self.quality_counts,
            "e2e_latency_us": {
                "p50": round(percentile(lat, 0.50), 1),
                "p95": round(percentile(lat, 0.95), 1),
                "p99": round(percentile(lat, 0.99), 1),
                "p999": round(percentile(lat, 0.999), 1),
                "max": round(lat[-1], 1) if lat else 0.0,
            },
            "processing_latency_us": {
                "p50": round(percentile(proc, 0.50), 1),
                "p95": round(percentile(proc, 0.95), 1),
                "p99": round(percentile(proc, 0.99), 1),
                "max": round(proc[-1], 1) if proc else 0.0,
            },
            "processing_latency_ns": {
                "p50": int(percentile(proc, 0.50) * 1000),
                "p95": int(percentile(proc, 0.95) * 1000),
                "p99": int(percentile(proc, 0.99) * 1000),
                "max": int((proc[-1] if proc else 0.0) * 1000),
            },
            "max_rss_mb": round(rss_mb, 1) if rss_mb else None,
        }

        if self._queue_depths:
            q_sorted = sorted(self._queue_depths)
            result["streaming"] = {
                "max_queue_depth": self.max_queue_depth,
                "p50_queue_depth": round(percentile(q_sorted, 0.50), 1),
                "p95_queue_depth": round(percentile(q_sorted, 0.95), 1),
                "backpressure_stalls": self.backpressure_stalls,
            }
            if self._storage_lags_us:
                sl_sorted = sorted(self._storage_lags_us)
                result["streaming"]["storage_lag_us"] = {
                    "p50": round(percentile(sl_sorted, 0.50), 1),
                    "p95": round(percentile(sl_sorted, 0.95), 1),
                    "max": round(sl_sorted[-1], 1),
                }

        return result

