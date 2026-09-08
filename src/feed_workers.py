"""
Multi-Feed Independent Ingestion Workers (Spec §18, Phase 2).

Provides isolated, dedicated OS worker threads for individual market venues
(Binance, Coinbase, Kraken, OKX, Bybit, Yahoo Equities, WebSockets, Simulators).
Each worker writes raw packets into its own lock-free SPSC ring buffer,
guaranteeing complete failure isolation: a slow, jittery, or reconnecting venue
never blocks or delays other market feeds or the central matching engine.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from models import RawEvent
from spsc_ring import SPSCRingBuffer

logger = logging.getLogger(__name__)


class BaseFeedWorker(threading.Thread, ABC):
    """Abstract base class for dedicated feed ingestion workers."""

    def __init__(self, name: str, queue_capacity: int = 16384):
        super().__init__(name=f"mdrap-feed-{name}", daemon=True)
        self.worker_name = name
        self.out_queue: SPSCRingBuffer[RawEvent] = SPSCRingBuffer(capacity=queue_capacity)
        self._stop_event = threading.Event()
        self._total_ingested = 0
        self._total_dropped = 0
        self._last_event_ts = 0.0
        self._is_connected = False
        self._errors_count = 0

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def stop(self, timeout: float = 2.0) -> None:
        """Signal worker to terminate and wait for thread to join."""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=timeout)

    @abstractmethod
    def run(self) -> None:
        """Main worker ingestion loop."""
        pass

    def emit(self, raw: RawEvent) -> bool:
        """Enqueue a raw event into this worker's SPSC output buffer."""
        ok = self.out_queue.offer(raw)
        if ok:
            self._total_ingested += 1
            self._last_event_ts = time.time()
        else:
            self._total_dropped += 1
        return ok

    def stats(self) -> dict:
        """Worker health and throughput telemetry."""
        return {
            "name": self.worker_name,
            "is_alive": self.is_alive(),
            "is_connected": self._is_connected,
            "queue_size": self.out_queue.size(),
            "queue_capacity": self.out_queue.capacity,
            "total_ingested": self._total_ingested,
            "total_dropped": self._total_dropped,
            "errors": self._errors_count,
            "last_event_ts": self._last_event_ts,
        }


class LiveExchangeFeedWorker(BaseFeedWorker):
    """
    Dedicated worker thread polling live exchange quotes & trades
    for specific venues and symbols (Binance, Coinbase, Kraken, OKX, Bybit, or Equities).
    """

    def __init__(
        self,
        name: str,
        symbols: List[str],
        poll_interval_s: float = 0.5,
        queue_capacity: int = 16384,
        timeout: float = 3.0,
    ):
        super().__init__(name=name, queue_capacity=queue_capacity)
        self.symbols = symbols
        self.poll_interval_s = poll_interval_s
        self.timeout = timeout

    def run(self) -> None:
        from live import LiveConnector, resolve_venue_symbols

        connector = LiveConnector(timeout=self.timeout)
        self._is_connected = True

        while not self._stop_event.is_set():
            for sym in self.symbols:
                if self._stop_event.is_set():
                    break
                try:
                    sym_info = resolve_venue_symbols(sym)
                    if sym_info["type"] == "EQUITY":
                        events = connector.fetch_equity_events(sym)
                        for ev in events:
                            self.emit(ev)
                    else:
                        raw = connector.fetch_quote(sym, self.worker_name)
                        if raw:
                            self.emit(raw)
                    self._is_connected = True
                except Exception as e:
                    self._errors_count += 1
                    self._is_connected = False
                    logger.debug(f"Feed worker {self.worker_name} fetch error: {e}")

            # Sleep poll interval with responsiveness to stop event
            self._stop_event.wait(self.poll_interval_s)


class SimulatorFeedWorker(BaseFeedWorker):
    """
    High-frequency synthetic market generator running on an isolated OS thread.
    Can generate up to 250,000 events/second without competing for locks.
    """

    def __init__(
        self,
        name: str = "SIM",
        symbols: Optional[List[str]] = None,
        target_eps: float = 10000.0,
        total_events: int = 0,
        seed: int = 42,
        queue_capacity: int = 32768,
    ):
        super().__init__(name=name, queue_capacity=queue_capacity)
        self.symbols = symbols or ["BTC/USD", "ETH/USD", "AAPL", "MSFT"]
        self.target_eps = target_eps
        self.total_events = total_events
        self.seed = seed

    def run(self) -> None:
        from simulator import FeedSimulator, SimulatorConfig

        sim_events = self.total_events if self.total_events > 0 else 100_000_000
        sim = FeedSimulator(SimulatorConfig(seed=self.seed, num_events=sim_events))
        delay = (1.0 / self.target_eps) if self.target_eps > 0 else 0.0
        self._is_connected = True

        sym_idx = 0
        gen = sim.generate()

        while not self._stop_event.is_set():
            try:
                raw, _ = next(gen)
            except StopIteration:
                break

            cur_sym = self.symbols[sym_idx % len(self.symbols)]
            sym_idx += 1
            if isinstance(raw.payload, dict):
                raw.payload["instrument"] = cur_sym

            # Spin-offer on ring buffer full so synthetic stream preserves integrity
            while not self.emit(raw):
                if self._stop_event.is_set():
                    return
                time.sleep(0.0001)

            if delay > 0.0001:
                time.sleep(delay)


class MultiFeedManager:
    """
    Orchestrates multiple independent feed workers and multiplexes their
    SPSC ring buffers into a unified downstream consumer stream.
    """

    def __init__(self):
        self._workers: List[BaseFeedWorker] = []
        self._worker_idx = 0

    @property
    def workers(self) -> List[BaseFeedWorker]:
        return self._workers

    def add_worker(self, worker: BaseFeedWorker) -> None:
        self._workers.append(worker)

    def start_all(self) -> None:
        """Start all registered feed worker threads."""
        for w in self._workers:
            if not w.is_alive():
                w.start()

    def stop_all(self, timeout: float = 2.0) -> None:
        """Gracefully stop and join all worker threads."""
        for w in self._workers:
            w.stop(timeout=timeout)
        self._workers.clear()

    def poll_next(self) -> Optional[RawEvent]:
        """
        Fast fair round-robin poll across all active feed workers' SPSC queues.
        Returns next RawEvent or None if all queues are currently empty.
        """
        num_workers = len(self._workers)
        if num_workers == 0:
            return None

        # Round-robin starting point
        start_idx = self._worker_idx
        for i in range(num_workers):
            idx = (start_idx + i) % num_workers
            ev = self._workers[idx].out_queue.poll()
            if ev is not None:
                self._worker_idx = (idx + 1) % num_workers
                return ev

        return None

    def drain_batch(self, target_list: List[RawEvent], max_items: int = 1000) -> int:
        """
        Drain up to max_items across all workers into target_list.
        Returns total number of items appended.
        """
        num_workers = len(self._workers)
        if num_workers == 0:
            return 0

        drained = 0
        quota_per_worker = max(10, max_items // max(1, num_workers))

        for w in self._workers:
            if drained >= max_items:
                break
            d = w.out_queue.drain_into(target_list, max_items=min(quota_per_worker, max_items - drained))
            drained += d

        return drained

    def total_queued(self) -> int:
        """Return sum of items currently buffered across all feed queues."""
        return sum(w.out_queue.size() for w in self._workers)

    def stats(self) -> dict:
        """Diagnostic summary of all managed feeds."""
        w_stats = [w.stats() for w in self._workers]
        return {
            "worker_count": len(self._workers),
            "total_queued": self.total_queued(),
            "total_ingested": sum(s["total_ingested"] for s in w_stats),
            "total_dropped": sum(s["total_dropped"] for s in w_stats),
            "workers": w_stats,
        }
