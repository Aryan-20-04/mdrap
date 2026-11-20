"""
MDRAP Sidecar Example: Decoupled TCP Streaming Socket Consumer.

Demonstrates an independent Python sidecar process subscribing to the local
MDRAP daemon via TCP socket using the production StreamClient API.

Features:
- Token-based authentication (MDRAP_DAEMON_TOKEN / AUTH command).
- Format negotiation (JSON line-delimited streaming).
- Live L1 tick and BBO consumption.
- On-demand L2 order book depth and health polling.
- Reconnection resilience with exponential backoff.
- Clean resource isolation and graceful shutdown.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any

# Ensure mdrap package is importable if run from repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mdrap.service import StreamClient


class SidecarSocketConsumer:
    """Independent sidecar client connected to MDRAP daemon over TCP."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        auth_token: str | None = None,
        symbol: str = "ALL",
    ):
        self.host = host
        self.port = port
        self.auth_token = auth_token or os.environ.get("MDRAP_DAEMON_TOKEN")
        self.symbol = symbol
        self.client: StreamClient | None = None
        self.running = False
        self.events_received = 0

    def connect(self, max_retries: int = 5, initial_backoff: float = 0.5) -> bool:
        """Establish authenticated connection to daemon with exponential backoff."""
        backoff = initial_backoff
        for attempt in range(1, max_retries + 1):
            try:
                print(f"[Sidecar-Socket] Connecting to {self.host}:{self.port} (attempt {attempt}/{max_retries})...")
                self.client = StreamClient(
                    host=self.host,
                    port=self.port,
                    auth_token=self.auth_token,
                    timeout=5.0,
                )
                self.client.connect()
                print(f"[Sidecar-Socket] Successfully connected and authenticated.")
                return True
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                print(f"[Sidecar-Socket] Connection failed: {exc}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 5.0)
            except PermissionError as auth_exc:
                print(f"[Sidecar-Socket] Authentication error: {auth_exc}. Check MDRAP_DAEMON_TOKEN.")
                return False
        return False

    def run(self, max_events: int = 0) -> None:
        """Main event streaming loop with automatic reconnection."""
        self.running = True
        start_time = time.perf_counter()

        while self.running:
            if not self.client:
                if not self.connect():
                    print("[Sidecar-Socket] Exhausted connection retries. Exiting.")
                    break

            try:
                # Query initial system status and health
                status = self.client.get_status()
                print(f"[Sidecar-Socket] Daemon Online | Ingestion Rate: {status.get('ingest_eps', 0):,.1f} eps | Broadcast Ticks: {status.get('broadcast_ticks', 0):,}")

                # Poll current L2 depth snapshot before streaming
                btc_depth = self.client.get_depth("BTC/USD")
                if btc_depth:
                    print(f"[Sidecar-Socket] Initial BTC/USD L2 Depth Snapshot: Mid={btc_depth.get('mid')}, Spread={btc_depth.get('spread')}")

                # Stream live normalized market ticks
                print(f"[Sidecar-Socket] Subscribing to stream '{self.symbol}'...")
                for tick in self.client.stream(symbol=self.symbol):
                    if not self.running:
                        break

                    self.events_received += 1
                    self._on_tick(tick)

                    if 0 < max_events <= self.events_received:
                        self.running = False
                        break

            except (ConnectionResetError, BrokenPipeError, TimeoutError) as net_exc:
                print(f"[Sidecar-Socket] Stream disconnected: {net_exc}. Reconnecting...")
                if self.client:
                    self.client.close()
                    self.client = None
                time.sleep(1.0)
            except Exception as exc:
                print(f"[Sidecar-Socket] Unexpected error: {exc}")
                break

        elapsed = time.perf_counter() - start_time
        eps = self.events_received / elapsed if elapsed > 0 else 0
        print(f"\n[Sidecar-Socket] Processed {self.events_received:,} events in {elapsed:.3f}s ({eps:,.1f} eps)")
        self.close()

    def _on_tick(self, tick: dict[str, Any]) -> None:
        """Process incoming market tick."""
        if self.events_received % 1000 == 0:
            print(f"[TCP TICK #{tick.get('seq')}] {tick.get('sym')} | Price: {tick.get('price')} | Status: {tick.get('status')} | Engine Latency: {tick.get('engine_us')} μs")

    def close(self) -> None:
        """Close connection and clean up resources."""
        self.running = False
        if self.client:
            self.client.close()
            self.client = None
            print("[Sidecar-Socket] Connection closed.")


def main():
    consumer = SidecarSocketConsumer(
        host="127.0.0.1",
        port=9876,
        symbol="ALL",
    )

    def _sig_handler(sig, frame):
        consumer.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    consumer.run(max_events=10_000)


if __name__ == "__main__":
    main()
