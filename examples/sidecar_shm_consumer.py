"""
MDRAP Sidecar Example: High-Performance Zero-Copy Shared Memory Consumer.

Demonstrates an independent, co-located downstream sidecar process consuming
L1 ticks and L2 depth events via lock-free shared memory ring buffer (SHM v3).

Features:
- Sub-microsecond IPC read latency (<0.5 μs).
- Two-phase commit seqlock validation (zero torn reads).
- Automatic publisher restart and epoch change handling.
- Overrun and slow-consumer lap accounting.
- Consumes both L1 ticks and L2 aggregated order-book depth frames.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any

# Ensure mdrap package is importable if run from repo root
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

from mdrap.shm import ReaderState, SHMReader


class SidecarSHMConsumer:
    """Independent sidecar consumer attached to MDRAP shared memory ring buffer."""

    def __init__(self, segment_name: str = "mdrap_feed", poll_delay_s: float = 0.0001):
        self.segment_name = segment_name
        self.poll_delay_s = poll_delay_s
        self.running = False
        self.reader: SHMReader | None = None
        self.ticks_received = 0
        self.depth_received = 0
        self.last_seq = 0

    def connect(self, max_retries: int = 10, retry_delay: float = 0.5) -> bool:
        """Attach to the named shared memory segment with retry backoff."""
        for attempt in range(1, max_retries + 1):
            try:
                self.reader = SHMReader(name=self.segment_name)
                print(
                    f"[Sidecar-SHM] Attached to '{self.segment_name}' (epoch: {self.reader.epoch_id}, slots: {self.reader.slot_count:,})"
                )
                return True
            except (FileNotFoundError, ValueError) as exc:
                print(
                    f"[Sidecar-SHM] Waiting for publisher daemon (attempt {attempt}/{max_retries}): {exc}"
                )
                time.sleep(retry_delay)
        return False

    def run(self, max_events: int = 0) -> None:
        """Main non-blocking event consumption loop."""
        if not self.reader:
            if not self.connect():
                print("[Sidecar-SHM] ERROR: Failed to attach to shared memory segment.")
                return

        self.running = True
        self.last_seq = self.reader.read_latest_seq()
        print(
            f"[Sidecar-SHM] Starting stream consumption from sequence {self.last_seq}..."
        )

        start_time = time.perf_counter()
        next_seq = self.last_seq + 1

        try:
            while self.running:
                # 1. Verify publisher epoch validity (detect daemon restarts)
                if not self.reader.check_epoch_valid():
                    print(
                        "[Sidecar-SHM] WARNING: Publisher restart or epoch change detected. Re-synchronizing..."
                    )
                    if self.reader._reattach():
                        next_seq = self.reader.read_latest_seq() + 1
                        print(
                            f"[Sidecar-SHM] Successfully reattached to new epoch {self.reader.epoch_id}. Resuming at seq {next_seq}"
                        )
                    else:
                        time.sleep(0.1)
                        continue

                # 2. Check head sequence
                latest_seq = self.reader.read_latest_seq()
                if latest_seq < next_seq:
                    # Writer has not produced next event yet; short backoff
                    if self.poll_delay_s > 0:
                        time.sleep(self.poll_delay_s)
                    continue

                # 3. Read sequence slot with two-phase commit seqlock verification
                item = self.reader.read_slot(next_seq)
                if item is None:
                    # Slot read returned None -> check if reader was lapped by fast producer
                    if self.reader.overrun_stats.total_laps > 0:
                        laps = self.reader.overrun_stats.total_laps
                        head = self.reader.read_latest_seq()
                        print(
                            f"[Sidecar-SHM] OVERRUN: Reader was lapped ({laps} total laps). Fast-forwarding from {next_seq} to head {head}"
                        )
                        next_seq = max(head - (self.reader.slot_count // 2), 1)
                        continue
                    else:
                        # Write still in flight or transient commit delay
                        time.sleep(self.poll_delay_s)
                        continue

                # 4. Dispatch event by type
                ev_type = item.get("type")
                if ev_type == "TICK":
                    self.ticks_received += 1
                    self._on_l1_tick(item)
                elif ev_type == "DEPTH":
                    self.depth_received += 1
                    self._on_l2_depth(item)

                next_seq += 1

                # Stop condition for bounded test runs
                total_events = self.ticks_received + self.depth_received
                if 0 < max_events <= total_events:
                    break

        except KeyboardInterrupt:
            print("\n[Sidecar-SHM] Interrupted by user.")
        finally:
            self.stop()
            elapsed = time.perf_counter() - start_time
            total = self.ticks_received + self.depth_received
            eps = total / elapsed if elapsed > 0 else 0
            print(
                f"\n[Sidecar-SHM] Summary: {total:,} events ({self.ticks_received:,} L1 ticks, {self.depth_received:,} L2 depth) in {elapsed:.3f}s ({eps:,.1f} eps)"
            )

    def _on_l1_tick(self, tick: dict[str, Any]) -> None:
        """Handle normalized L1 market tick."""
        # Institutional user callback: feed trading logic, risk checks, or audit logs
        if self.ticks_received % 1000 == 0:
            print(
                f"[L1 TICK #{tick['seq']}] {tick['sym']} | Price: {tick.get('price')} | Status: {tick.get('status')} | Engine Latency: {tick.get('engine_us')} μs"
            )

    def _on_l2_depth(self, depth: dict[str, Any]) -> None:
        """Handle aggregated L2 book depth update."""
        # Institutional user callback: update order book representation
        if self.depth_received % 500 == 0:
            print(
                f"[L2 DEPTH #{depth['seq']}] {depth['sym']} | MicroPrice: {depth.get('micro_price')} | OFI: {depth.get('ofi')} | Crossed: {depth.get('is_crossed')}"
            )

    def stop(self) -> None:
        """Detach from shared memory and release resources."""
        self.running = False
        if self.reader:
            self.reader.close()
            self.reader = None
            print("[Sidecar-SHM] Detached and closed shared memory reader.")


def main():
    consumer = SidecarSHMConsumer(segment_name="mdrap_feed")

    def _sig_handler(sig, frame):
        consumer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    consumer.run(max_events=10_000)


if __name__ == "__main__":
    main()
