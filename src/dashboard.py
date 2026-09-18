"""
Live terminal dashboard (rich). Deliberately not a web UI: the spec
calls for an API/WebSocket surface eventually, but for the MVP the
priority is speed and operability, so observability lives directly in
the terminal that's already running the pipeline.
"""

from __future__ import annotations

import time
from typing import Optional

from rich.console import Group
from rich.live import Live
from term import Table, Panel, format_status

from metrics import LIVE_WINDOW, percentile
from pipeline import Pipeline


def _quality_table(pipeline: Pipeline) -> Table:
    t = Table(title="Quality Classification", expand=True)
    t.add_column("Status")
    t.add_column("Count", justify="right")
    counts = pipeline.metrics.quality_counts
    total = max(1, sum(counts.values()))
    for status in ("VALID", "SUSPICIOUS", "INVALID"):
        c = counts.get(status, 0)
        t.add_row(format_status(status), f"{c:,} ({100 * c / total:.2f}%)")
    return t


def _reason_table(pipeline: Pipeline) -> Table:
    t = Table(title="Reason Codes (SUSPICIOUS/INVALID)", expand=True)
    t.add_column("Reason")
    t.add_column("Count", justify="right")
    for reason, count in sorted(
        pipeline.quality.reason_counts.items(), key=lambda kv: -kv[1]
    ):
        t.add_row(reason, f"{count:,}")
    return t


def _source_table(pipeline: Pipeline) -> Table:
    t = Table(title="Source Reliability", expand=True)
    t.add_column("Source")
    t.add_column("Total", justify="right")
    t.add_column("Error %", justify="right")
    t.add_column("Dup %", justify="right")
    t.add_column("EWMA Latency (ms)", justify="right")
    t.add_column("Score", justify="right")
    for src, st in sorted(pipeline.reliability.stats.items()):
        t.add_row(
            src,
            f"{st.total:,}",
            f"{100 * st.error_rate:.2f}",
            f"{100 * st.duplicate / max(1, st.total):.3f}",
            f"{st.ewma_latency_s * 1000:.3f}",
            f"{st.score:.4f}",
        )
    return t


def _perf_panel(pipeline: Pipeline, target_events: Optional[int]) -> Panel:
    m = pipeline.metrics
    # Live view uses the bounded recent-window deques, not the full
    # cumulative history -- sorting the full list on every refresh made
    # the dashboard itself the bottleneck at scale (measured: ~6.5s of
    # pure sort time over a 200k-event run refreshed every 500 events).
    lat = sorted(m.recent_latencies_us)
    proc = sorted(m.recent_proc_latencies_us)
    elapsed = time.time() - m.start_time
    tput = m.processed / elapsed if elapsed > 0 else 0
    progress = f" ({100 * m.processed / target_events:.1f}%)" if target_events else ""
    window_note = f" (last {len(lat):,})" if m.processed > LIVE_WINDOW else ""
    text = (
        f"[bold]Processed:[/bold] {m.processed:,}{progress}   "
        f"[bold]Elapsed:[/bold] {elapsed:.2f}s   "
        f"[bold]Throughput:[/bold] {tput:,.0f} events/sec\n"
        f"[bold]E2E latency (us) p50/p95/p99/max{window_note}:[/bold] "
        f"{percentile(lat, 0.5):.0f} / {percentile(lat, 0.95):.0f} / "
        f"{percentile(lat, 0.99):.0f} / {(lat[-1] if lat else 0):.0f}\n"
        f"[bold]Processing latency (us) p50/p95/p99{window_note}:[/bold] "
        f"{percentile(proc, 0.5):.0f} / {percentile(proc, 0.95):.0f} / {percentile(proc, 0.99):.0f}"
    )
    return Panel(text, title="Performance", border_style="cyan")


def render(pipeline: Pipeline, target_events: Optional[int] = None) -> Group:
    return Group(
        _perf_panel(pipeline, target_events),
        _quality_table(pipeline),
        _reason_table(pipeline),
        _source_table(pipeline),
    )


class Dashboard:
    """Wraps rich.Live so the caller just calls .refresh() periodically."""

    def __init__(
        self,
        pipeline: Pipeline,
        target_events: Optional[int] = None,
        refresh_hz: float = 8,
    ):
        self.pipeline = pipeline
        self.target_events = target_events
        self._live = Live(
            render(pipeline, target_events), refresh_per_second=refresh_hz, screen=False
        )

    def __enter__(self):
        self._live.__enter__()
        return self

    def __exit__(self, *a):
        self._live.__exit__(*a)

    def refresh(self):
        self._live.update(render(self.pipeline, self.target_events))
