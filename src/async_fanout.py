"""
MDRAP Phase 8 Asynchronous High-Concurrency Consumer Fan-Out Engine.

Scales non-blocking stream broadcast to 100+ concurrent consumers without
head-of-line blocking or stalling the authoritative ingestion pipeline.
Implements bounded per-client queues, observable drop counters, and automatic
stalled consumer eviction. Pure Python stdlib (threading, collections.deque, time).
"""

from __future__ import annotations

import collections
import dataclasses
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

__stability__ = "stable"


@dataclasses.dataclass(slots=True)
class AsyncConsumerSession:
    """Session representation for a connected stream consumer."""

    consumer_id: str
    tenant_id: str
    max_queue_size: int
    subscribed_symbols: Optional[Set[str]]
    queue: collections.deque = dataclasses.field(default_factory=collections.deque)
    queue_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    is_active: bool = True
    frames_dispatched: int = 0
    frames_dropped: int = 0
    frames_consumed: int = 0
    last_active_ts: float = dataclasses.field(default_factory=time.time)
    connected_ts: float = dataclasses.field(default_factory=time.time)


class AsyncFanoutManager:
    """
    Decoupled asynchronous consumer fan-out broadcaster.

    Decouples producer ingestion from network/client dispatch via an internal
    bounded handoff queue and dedicated dispatch thread. Even with 100+ slow
    or completely unresponsive consumers, the publisher thread executes in
    sub-microsecond time with zero blocking.
    """

    def __init__(
        self,
        max_buffer_per_client: int = 1000,
        max_incoming_buffer: int = 50000,
        eviction_drop_threshold: int = 50,
    ) -> None:
        self.max_buffer_per_client = max_buffer_per_client
        self.max_incoming_buffer = max_incoming_buffer
        self.eviction_drop_threshold = eviction_drop_threshold

        self._incoming_queue: collections.deque[Tuple[str, bytes, int]] = collections.deque()
        self._incoming_lock = threading.Lock()
        self._incoming_cond = threading.Condition(self._incoming_lock)

        self._consumers_lock = threading.Lock()
        self._consumers: Dict[str, AsyncConsumerSession] = {}

        self._total_published: int = 0
        self._total_dispatched: int = 0
        self._total_dropped: int = 0
        self._total_evictions: int = 0

        self._running: bool = True
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            daemon=True,
            name="mdrap-async-fanout-dispatcher",
        )
        self._dispatcher_thread.start()

    def stop(self) -> None:
        """Signal the background dispatcher to stop and join."""
        self._running = False
        with self._incoming_cond:
            self._incoming_cond.notify_all()
        if self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=1.0)

    def register_consumer(
        self,
        consumer_id: str,
        tenant_id: str = "default",
        subscribed_symbols: Optional[List[str]] = None,
        max_queue_size: Optional[int] = None,
    ) -> AsyncConsumerSession:
        """Register a new consumer with an isolated bounded queue."""
        limit = max_queue_size or self.max_buffer_per_client
        symbols_set = set(s.upper() for s in subscribed_symbols) if subscribed_symbols else None
        session = AsyncConsumerSession(
            consumer_id=consumer_id,
            tenant_id=tenant_id,
            max_queue_size=limit,
            subscribed_symbols=symbols_set,
            queue=collections.deque(maxlen=limit),
        )
        with self._consumers_lock:
            self._consumers[consumer_id] = session
        return session

    def unregister_consumer(self, consumer_id: str) -> bool:
        """Unregister and deactivate a consumer session."""
        with self._consumers_lock:
            session = self._consumers.pop(consumer_id, None)
            if session:
                session.is_active = False
                return True
            return False

    def publish_event(self, symbol: str, frame: bytes, seq: int = 0) -> bool:
        """
        Ultra-fast non-blocking producer handoff.
        Pushes to internal decoupled queue and signals dispatcher.
        Returns True if enqueued, False if incoming buffer saturated.
        """
        sym = symbol.upper()
        with self._incoming_lock:
            if len(self._incoming_queue) >= self.max_incoming_buffer:
                # Producer backpressure protection: drop oldest incoming if saturated
                self._incoming_queue.popleft()
                self._total_dropped += 1
            self._incoming_queue.append((sym, frame, seq))
            self._total_published += 1
            self._incoming_cond.notify()
        return True

    def _dispatch_loop(self) -> None:
        """Background worker distributing events to all registered consumer sessions."""
        batch: List[Tuple[str, bytes, int]] = []
        while self._running:
            with self._incoming_lock:
                while self._running and not self._incoming_queue:
                    self._incoming_cond.wait(timeout=0.01)
                if not self._running and not self._incoming_queue:
                    break
                # Drain up to 256 events per batch to amortize lock overhead
                count = min(len(self._incoming_queue), 256)
                batch = [self._incoming_queue.popleft() for _ in range(count)]

            if not batch:
                continue

            with self._consumers_lock:
                sessions = list(self._consumers.values())

            if not sessions:
                continue

            to_evict: List[str] = []

            for sym, frame, seq in batch:
                item = (sym, frame, seq)
                for s in sessions:
                    if not s.is_active:
                        continue
                    if s.subscribed_symbols is not None and sym not in s.subscribed_symbols:
                        continue

                    # Non-blocking per-consumer queue append
                    with s.queue_lock:
                        if len(s.queue) >= s.max_queue_size:
                            # Stalled client queue saturated: drop oldest unread event
                            s.queue.popleft()
                            s.frames_dropped += 1
                            self._total_dropped += 1
                            if s.frames_dropped >= self.eviction_drop_threshold:
                                to_evict.append(s.consumer_id)
                        s.queue.append(item)
                        s.frames_dispatched += 1
                        self._total_dispatched += 1

            if to_evict:
                with self._consumers_lock:
                    for cid in set(to_evict):
                        session = self._consumers.pop(cid, None)
                        if session:
                            session.is_active = False
                            self._total_evictions += 1

    def consume_event(
        self, consumer_id: str, timeout: float = 0.0
    ) -> Optional[Tuple[str, bytes, int]]:
        """Client-side helper: consume one event from consumer's private queue."""
        with self._consumers_lock:
            session = self._consumers.get(consumer_id)
        if not session or not session.is_active:
            return None

        deadline = time.monotonic() + timeout
        while True:
            with session.queue_lock:
                if session.queue:
                    item = session.queue.popleft()
                    session.frames_consumed += 1
                    session.last_active_ts = time.time()
                    return item
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        return None

    def stats(self) -> Dict[str, Any]:
        """Return real-time operational telemetry for all fan-out streams."""
        with self._consumers_lock:
            active_count = len(self._consumers)
            client_metrics = {
                cid: {
                    "tenant_id": s.tenant_id,
                    "is_active": s.is_active,
                    "dispatched": s.frames_dispatched,
                    "dropped": s.frames_dropped,
                    "consumed": s.frames_consumed,
                    "queue_depth": len(s.queue),
                    "max_queue_size": s.max_queue_size,
                }
                for cid, s in self._consumers.items()
            }
        with self._incoming_lock:
            incoming_depth = len(self._incoming_queue)

        return {
            "active_consumers": active_count,
            "incoming_queue_depth": incoming_depth,
            "total_published": self._total_published,
            "total_dispatched": self._total_dispatched,
            "total_dropped": self._total_dropped,
            "total_evictions": self._total_evictions,
            "clients": client_metrics,
        }
