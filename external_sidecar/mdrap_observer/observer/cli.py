"""CLI entry point for MDRAP Observer."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from typing import Any, Optional

from .client import ObserverClient
from .dashboard import ObserverDashboard
from .exporter import ObserverExporter
from .state import ObserverState


def build_health_snapshot(
    state: ObserverState,
    exporter: ObserverExporter,
    start_time: float,
    client: Optional[ObserverClient] = None,
) -> dict[str, Any]:
    """Construct periodic health snapshot atomically with stream, venue, and exporter telemetry."""
    state_snap = state.snapshot_health()
    exp_stats = exporter.snapshot_stats() if hasattr(exporter, "snapshot_stats") else {
        "events_written": exporter.events_written,
        "health_written": exporter.health_written,
        "dropped_records": exporter.dropped_records,
        "write_errors": exporter.write_errors,
        "queue_size": exporter._queue.qsize(),
    }
    snap: dict[str, Any] = {
        "uptime": time.time() - start_time,
        "eps": state_snap["eps"],
        "rss_mb": state.get_rss_mb(),
        "stream": state_snap["stream"],
        "venues": state_snap["venues"],
        "exporter_events_written": exp_stats["events_written"],
        "exporter_health_written": exp_stats["health_written"],
        "exporter_flushed_records": exp_stats.get("flushed_records", 0),
        "exporter_fsynced_records": exp_stats.get("fsynced_records", 0),
        "exporter_dropped_records": exp_stats["dropped_records"],
        "exporter_failed_records": exp_stats.get("failed_records", 0),
        "exporter_write_errors": exp_stats["write_errors"],
        "exporter_queue_size": exp_stats["queue_size"],
    }
    if client is not None:
        snap["transport_reconnects"] = client.reconnect_count
    return snap


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mdrap-observer",
        description="MDRAP Observer: Real-time Multi-Venue Read-Only Market Data Cockpit",
    )
    parser.add_argument("--mode", choices=["tcp", "shm"], default="tcp", help="Transport mode (default: tcp)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="MDRAP daemon host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="MDRAP daemon port (default: 9000)")
    parser.add_argument("--ring", type=str, default="mdrap_canonical_ring", help="Shared memory ring buffer name")
    parser.add_argument("--export", type=str, default="./observer_exports", help="JSONL export directory")
    parser.add_argument("--refresh", type=float, default=0.5, help="Dashboard refresh rate in seconds")
    parser.add_argument("--duration", type=float, default=0.0, help="Run duration in seconds (0 = run indefinitely)")
    parser.add_argument("--stale-threshold", type=float, default=3.0, help="Venue inactivity stale threshold in seconds")
    parser.add_argument("--no-ui", action="store_true", help="Run headlessly without rendering terminal UI")

    args = parser.parse_args(argv)

    state = ObserverState() if args.stale_threshold == 3.0 else ObserverState(stale_threshold_s=args.stale_threshold)
    exporter = ObserverExporter(output_dir=args.export)
    dashboard = ObserverDashboard(state=state, mode=args.mode, exporter=exporter)

    def on_event(event):
        state.process_event(event)
        exporter.record_event(event)

    client = ObserverClient(
        mode=args.mode,
        host=args.host,
        port=args.port,
        ring_name=args.ring,
        on_event_callback=on_event,
    )

    running = True

    def handle_sigint(sig, frame):
        nonlocal running
        running = False

    prev_sigint = None
    if threading.current_thread() is threading.main_thread():
        prev_sigint = signal.signal(signal.SIGINT, handle_sigint)

    exporter.start()
    client.start()

    start_time = time.time()
    try:
        while running:
            state.check_all_stale()

            if not args.no_ui:
                dashboard.render()

            # Record periodic health snapshots to exporter
            snapshot = build_health_snapshot(
                state=state, exporter=exporter, start_time=start_time, client=client
            )
            exporter.record_health(snapshot)

            if args.duration > 0 and (time.time() - start_time) >= args.duration:
                break

            time.sleep(args.refresh)

    finally:
        if prev_sigint is not None and threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, prev_sigint)
        client.stop()
        stop_result = exporter.stop()
        if isinstance(stop_result, dict) and not stop_result.get("drained", True):
            print(
                f"WARNING: exporter did not drain cleanly: remaining_queue={stop_result.get('remaining_queue')}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
