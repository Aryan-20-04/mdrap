"""
Unified High-Throughput Streaming Feed Supervisor for MDRAP.

Coordinates and supervises multi-source direct market data streaming handlers:
- Crypto WebSockets (Binance, Coinbase, Kraken, OKX, Bybit) via ws_feed.py
- Polygon.io Streaming WebSocket (US Equities & Crypto) via polygon_feed.py
- Databento Binary Encoding (DBN) Streamer (CME, Nasdaq, BBO) via databento_feed.py
- Deterministic Feed Simulator via simulator.py

Features:
- Multiplexes disparate feeds into a single unified RawEvent stream.
- Sub-millisecond queue bridging network I/O threads to synchronous MDRAP pipeline.
- High-watermark ring eviction to guarantee fresh, non-stale market feeds.
- Aggregated real-time ingestion telemetry (total events, eps, dropped packets, uptime).
"""
from __future__ import annotations

import enum
import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from databento_feed import DatabentoFeedManager
from models import RawEvent
from polygon_feed import PolygonFeedManager
from ws_feed import WebSocketFeedManager, HAS_WEBSOCKETS

logger = logging.getLogger("mdrap.feed_handler")


class FeedProvider(str, enum.Enum):
    CRYPTO = "crypto"
    POLYGON = "polygon"
    DATABENTO = "databento"
    SIMULATOR = "simulator"
    ALL = "all"


@dataclass
class FeedSupervisorConfig:
    provider: FeedProvider = FeedProvider.CRYPTO
    symbols: List[str] = field(default_factory=lambda: ["BTC/USD", "ETH/USD", "AAPL", "NVDA"])
    max_queue_size: int = 50000
    mock_mode: bool = False
    polygon_key: Optional[str] = None
    databento_key: Optional[str] = None
    databento_file: Optional[str] = None
    databento_dataset: str = "GLBX.MDP3"
    databento_schema: str = "mbp-1"
    crypto_venues: Optional[List[str]] = None


class StreamingFeedSupervisor:
    """
    Unified Ingestion Supervisor managing active streaming providers.
    Directly feeds the MDRAP pipeline, depth engines, and live cockpits.
    """

    def __init__(self, config: Optional[FeedSupervisorConfig] = None):
        self.config = config or FeedSupervisorConfig()
        self._queue: queue.Queue[RawEvent] = queue.Queue(maxsize=self.config.max_queue_size)
        self._stop_event = threading.Event()
        self._workers: List[Any] = []
        self._bridge_threads: List[threading.Thread] = []

        self._start_time = 0.0
        self._total_events = 0
        self._dropped_events = 0

        self._init_providers()

    def _init_providers(self) -> None:
        p = self.config.provider
        syms = self.config.symbols

        # 1. Crypto WebSocket Manager
        if p in (FeedProvider.CRYPTO, FeedProvider.ALL):
            crypto_syms = [s for s in syms if any(c in s.upper() for c in ("BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "USD"))]
            if not crypto_syms:
                crypto_syms = ["BTC/USD", "ETH/USD"]
            ws_mgr = WebSocketFeedManager(
                symbols=crypto_syms,
                venues=self.config.crypto_venues,
                max_queue_size=self.config.max_queue_size // 2,
            )
            self._workers.append(("crypto", ws_mgr))

        # 2. Polygon Feed Manager
        if p in (FeedProvider.POLYGON, FeedProvider.ALL):
            poly_syms = [s for s in syms if "/" not in s and not s.endswith("-USD")]
            if not poly_syms:
                poly_syms = [s for s in syms]
            poly_mgr = PolygonFeedManager(
                symbols=poly_syms,
                api_key=self.config.polygon_key,
                mock_mode=self.config.mock_mode,
                max_queue_size=self.config.max_queue_size // 2,
            )
            self._workers.append(("polygon", poly_mgr))

        # 3. Databento Feed Manager
        if p in (FeedProvider.DATABENTO, FeedProvider.ALL):
            dbn_mgr = DatabentoFeedManager(
                symbols=syms,
                api_key=self.config.databento_key,
                dataset=self.config.databento_dataset,
                schema=self.config.databento_schema,
                file_path=self.config.databento_file,
                mock_mode=self.config.mock_mode,
                max_queue_size=self.config.max_queue_size // 2,
            )
            self._workers.append(("databento", dbn_mgr))

    def start(self) -> None:
        """Start all configured streaming feed workers."""
        if self.is_running():
            return

        self._stop_event.clear()
        self._start_time = time.time()
        self._total_events = 0
        self._dropped_events = 0

        for name, worker in self._workers:
            worker.start()
            t = threading.Thread(
                target=self._bridge_worker,
                args=(name, worker),
                daemon=True,
                name=f"mdrap-bridge-{name}",
            )
            t.start()
            self._bridge_threads.append(t)

    def stop(self) -> None:
        """Stop all active feed workers."""
        self._stop_event.set()
        for name, worker in self._workers:
            try:
                worker.stop()
            except Exception:
                pass

        for t in self._bridge_threads:
            if t.is_alive():
                t.join(timeout=1.0)
        self._bridge_threads.clear()

    def is_running(self) -> bool:
        return any(w.is_running() for _, w in self._workers) and not self._stop_event.is_set()

    def _enqueue(self, ev: RawEvent) -> None:
        try:
            self._queue.put_nowait(ev)
            self._total_events += 1
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(ev)
            self._dropped_events += 1
            self._total_events += 1

    def _bridge_worker(self, name: str, worker: Any) -> None:
        """Continuously pulls from a specific worker's queue and enqueues into supervisor."""
        stream = worker.stream_events(timeout_s=0.5)
        for ev in stream:
            if self._stop_event.is_set():
                break
            self._enqueue(ev)

    def stream_events(
        self,
        limit: Optional[int] = None,
        timeout_s: float = 2.0,
    ) -> Generator[RawEvent, None, None]:
        """Synchronously yield RawEvents from the supervisor for MDRAP pipeline ingestion."""
        count = 0
        while not self._stop_event.is_set():
            try:
                ev = self._queue.get(timeout=timeout_s)
                yield ev
                count += 1
                if limit and count >= limit:
                    return
            except queue.Empty:
                if not self.is_running():
                    return

    def stats(self) -> dict:
        """Return unified telemetry across all supervised feeds."""
        now = time.time()
        elapsed = max(0.001, now - self._start_time) if self._start_time > 0 else 1.0
        current_eps = round(self._total_events / elapsed, 1)

        worker_stats = {}
        for name, worker in self._workers:
            try:
                worker_stats[name] = worker.stats()
            except Exception:
                worker_stats[name] = {}

        return {
            "is_running": self.is_running(),
            "uptime_s": round(elapsed, 1),
            "total_events": self._total_events,
            "dropped_events": self._dropped_events,
            "current_eps": current_eps,
            "queue_size": self._queue.qsize(),
            "providers": worker_stats,
        }
