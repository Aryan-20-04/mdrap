"""
Independent External Market Data Sidecar Consumer Application.

This application runs as an external, decoupled process consuming market data
from the MDRAP sidecar platform using ONLY public, documented interfaces:
- `mdrap.shm.SHMReader` for ultra-low latency shared memory.
- `mdrap.service.StreamClient` for TCP stream distribution.

NO internal MDRAP modules or private implementation details are imported.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import statistics
import sys
import threading
import time
from typing import Any

# STRICT ENFORCEMENT: Only public interfaces are imported
from mdrap.service import StreamClient
from mdrap.shm import SHMReader

logger = logging.getLogger("external_sidecar")


class IndependentSidecarConsumer:
    """
    Independent downstream trading/risk sidecar application.
    Consumes L1 market ticks and L2 depth via SHM and TCP simultaneously.
    """

    def __init__(
        self,
        shm_name: str = "mdrap_feed",
        tcp_host: str = "127.0.0.1",
        tcp_port: int = 9876,
        auth_token: str | None = None,
        enable_shm: bool = True,
        enable_tcp: bool = True,
    ):
        self.shm_name = shm_name
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.auth_token = auth_token or os.environ.get("MDRAP_DAEMON_TOKEN")
        self.enable_shm = enable_shm
        self.enable_tcp = enable_tcp

        self.running = False
        self._shm_reader: SHMReader | None = None
        self._stream_client: StreamClient | None = None

        # Metrics & Accounting
        self.lock = threading.Lock()
        self.shm_ticks = 0
        self.shm_depths = 0
        self.shm_torn_reads = 0
        self.shm_seq_gaps = 0
        self.shm_laps = 0
        self.shm_reconnects = 0
        self.shm_last_seq = 0
        self.shm_latencies_us: list[float] = []

        self.tcp_ticks = 0
        self.tcp_seq_gaps = 0
        self.tcp_reconnects = 0
        self.tcp_last_seq = 0
        self.tcp_latencies_us: list[float] = []

        self.consistency_checks_passed = 0
        self.consistency_checks_failed = 0
        self.last_known_bbo: dict[str, dict[str, float]] = {}

    def start(self) -> None:
        """Start consumption threads for enabled interfaces."""
        self.running = True
        self._threads: list[threading.Thread] = []

        if self.enable_shm:
            t_shm = threading.Thread(
                target=self._shm_worker, daemon=True, name="sidecar-shm"
            )
            t_shm.start()
            self._threads.append(t_shm)

        if self.enable_tcp:
            t_tcp = threading.Thread(
                target=self._tcp_worker, daemon=True, name="sidecar-tcp"
            )
            t_tcp.start()
            self._threads.append(t_tcp)

    def stop(self) -> None:
        """Stop all workers and close connections."""
        self.running = False
        if self._stream_client:
            try:
                self._stream_client.close()
            except Exception:
                pass
        if self._shm_reader:
            try:
                self._shm_reader.close()
            except Exception:
                pass
        for t in getattr(self, "_threads", []):
            if t.is_alive():
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass

    def _shm_worker(self) -> None:
        """Shared memory event consumption loop with epoch recovery & lap handling."""
        while self.running:
            if not self._shm_reader:
                try:
                    self._shm_reader = SHMReader(name=self.shm_name)
                    with self.lock:
                        self.shm_last_seq = self._shm_reader.read_latest_seq()
                except Exception:
                    time.sleep(0.05)
                    continue

            # Verify epoch validity (detect daemon restarts)
            if not self._shm_reader.check_epoch_valid():
                with self.lock:
                    self.shm_reconnects += 1
                if self._shm_reader._reattach():
                    with self.lock:
                        self.shm_last_seq = self._shm_reader.read_latest_seq()
                time.sleep(0.01)
                continue

            next_seq = self.shm_last_seq + 1
            latest = self._shm_reader.read_latest_seq()

            if latest < next_seq:
                time.sleep(0.0001)
                continue

            item = self._shm_reader.read_slot(next_seq)
            now = time.time()

            if item is None:
                # Check for buffer wraparound lap
                laps = self._shm_reader.overrun_stats.total_laps
                if laps > self.shm_laps:
                    with self.lock:
                        self.shm_laps = laps
                        self.shm_last_seq = self._shm_reader.read_latest_seq()
                    continue
                else:
                    time.sleep(0.0001)
                    continue

            # Process valid event
            with self.lock:
                ev_type = item.get("type")
                seq = item.get("seq", 0)

                if seq != next_seq and self.shm_last_seq > 0:
                    self.shm_seq_gaps += 1

                self.shm_last_seq = seq
                bc_ts = item.get("broadcast_ts", 0.0)
                if bc_ts > 0:
                    lat_us = max(0.0, (now - bc_ts) * 1_000_000.0)
                    self.shm_latencies_us.append(lat_us)
                    if len(self.shm_latencies_us) > 50_000:
                        self.shm_latencies_us = self.shm_latencies_us[-25_000:]

                sym = item.get("sym", "")
                status = item.get("status", "VALID")
                if ev_type == "TICK":
                    self.shm_ticks += 1
                    # Verify L1 consistency: quotes must have ask >= bid unless marked crossed/invalid
                    bid = item.get("bid")
                    ask = item.get("ask")
                    if bid is not None and ask is not None:
                        if ask >= bid or status != "VALID" or item.get("is_crossed"):
                            self.consistency_checks_passed += 1
                        else:
                            self.consistency_checks_failed += 1
                elif ev_type == "DEPTH":
                    self.shm_depths += 1
                    best_bids = item.get("bids", [])
                    best_asks = item.get("asks", [])
                    if best_bids and best_asks:
                        bb = best_bids[0][0]
                        ba = best_asks[0][0]
                        if ba >= bb or item.get("is_crossed") or status != "VALID":
                            self.consistency_checks_passed += 1
                        else:
                            self.consistency_checks_failed += 1
                        self.last_known_bbo[sym] = {"bid": bb, "ask": ba}

    def _tcp_worker(self) -> None:
        """TCP streaming socket worker with automatic reconnection."""
        while self.running:
            if not self._stream_client:
                try:
                    client = StreamClient(
                        host=self.tcp_host,
                        port=self.tcp_port,
                        auth_token=self.auth_token,
                        timeout=5.0,
                    )
                    client.connect()
                    self._stream_client = client
                except Exception:
                    time.sleep(0.2)
                    continue

            try:
                for tick in self._stream_client.stream(symbol="ALL"):
                    if not self.running:
                        break
                    now = time.time()
                    with self.lock:
                        self.tcp_ticks += 1
                        seq = tick.get("seq", 0)
                        if self.tcp_last_seq > 0 and seq != self.tcp_last_seq + 1:
                            self.tcp_seq_gaps += 1
                        self.tcp_last_seq = seq

                        bc_ts = tick.get("broadcast_ts", 0.0)
                        if bc_ts > 0:
                            lat_us = max(0.0, (now - bc_ts) * 1_000_000.0)
                            self.tcp_latencies_us.append(lat_us)
                            if len(self.tcp_latencies_us) > 50_000:
                                self.tcp_latencies_us = self.tcp_latencies_us[-25_000:]
            except Exception:
                with self.lock:
                    self.tcp_reconnects += 1
                if self._stream_client:
                    try:
                        self._stream_client.close()
                    except Exception:
                        pass
                    self._stream_client = None
                time.sleep(0.2)

    def stats(self) -> dict[str, Any]:
        """Compute aggregate delivery and latency statistics."""
        with self.lock:

            def _calc_stats(lats: list[float]) -> dict[str, float]:
                if not lats:
                    return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "p999": 0.0, "max": 0.0}
                s = sorted(lats)
                n = len(s)
                return {
                    "p50": round(s[int(n * 0.50)], 2),
                    "p95": round(s[min(int(n * 0.95), n - 1)], 2),
                    "p99": round(s[min(int(n * 0.99), n - 1)], 2),
                    "p999": round(s[min(int(n * 0.999), n - 1)], 2),
                    "max": round(s[-1], 2),
                }

            return {
                "shm": {
                    "ticks": self.shm_ticks,
                    "depths": self.shm_depths,
                    "total_events": self.shm_ticks + self.shm_depths,
                    "torn_reads": self.shm_torn_reads,
                    "seq_gaps": self.shm_seq_gaps,
                    "laps": self.shm_laps,
                    "reconnects": self.shm_reconnects,
                    "latency_us": _calc_stats(self.shm_latencies_us),
                },
                "tcp": {
                    "ticks": self.tcp_ticks,
                    "seq_gaps": self.tcp_seq_gaps,
                    "reconnects": self.tcp_reconnects,
                    "latency_us": _calc_stats(self.tcp_latencies_us),
                },
                "consistency": {
                    "passed": self.consistency_checks_passed,
                    "failed": self.consistency_checks_failed,
                },
            }
