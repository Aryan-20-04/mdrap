"""CLI entry point for MDRAP Observer."""

from __future__ import annotations

import argparse
import signal
import sys
import time

from .client import ObserverClient
from .dashboard import ObserverDashboard
from .exporter import ObserverExporter
from .state import ObserverState


def main() -> int:
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
    parser.add_argument("--no-ui", action="store_true", help="Run headlessly without rendering terminal UI")

    args = parser.parse_args()

    state = ObserverState()
    exporter = ObserverExporter(output_dir=args.export)
    dashboard = ObserverDashboard(state=state, mode=args.mode)

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

    signal.signal(signal.SIGINT, handle_sigint)

    exporter.start()
    client.start()

    start_time = time.time()
    try:
        while running:
            if not args.no_ui:
                dashboard.render()

            # Record periodic health snapshots to exporter
            snapshot = {
                "uptime": time.time() - start_time,
                "eps": state.get_throughput_eps(),
                "rss_mb": state.get_rss_mb(),
                "venues": {
                    v: {
                        "status": state.health[v].status,
                        "events": state.health[v].events_received,
                        "gaps": state.health[v].gaps,
                        "reconnects": state.health[v].reconnects,
                    }
                    for v in state.health
                },
            }
            exporter.record_health(snapshot)

            if args.duration > 0 and (time.time() - start_time) >= args.duration:
                break

            time.sleep(args.refresh)

    finally:
        client.stop()
        exporter.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
