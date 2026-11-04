"""MDRAP Phase 6 — Partitioning, Horizontal Scaling, and Fan-Out Routing.

Provides deterministic symbol partitioning, decoupled bounded consumer fan-out,
tenant quota governance, and fleet health aggregation for multi-instance scaling.
"""

from __future__ import annotations

__stability__ = "stable"

import collections
import dataclasses
import queue
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .models import QualityStatus, Reason


@dataclasses.dataclass(frozen=True)
class ShardConfig:
    """Configuration for an individual partitioned MDRAP shard."""

    shard_id: int
    name: str
    symbol_prefix_start: str = "A"
    symbol_prefix_end: str = "Z"
    sbe_port: int = 9002
    data_dir: str = "/var/data/mdrap"
    max_queue_depth: int = 50000


class SpscRingQueue:
    """Bounded, lock-free Single-Producer Single-Consumer (SPSC) circular ring buffer.

    Enforces ADR-004: Eliminates mutex / Condition variable contention on the single-producer
    to single-consumer event pipeline.
    """

    def __init__(self, capacity: int = 65536) -> None:
        cap = 1
        while cap < capacity:
            cap <<= 1
        self.capacity = cap
        self.mask = cap - 1
        self._buffer: List[Any] = [None] * cap
        self._head: int = 0
        self._tail: int = 0

    def offer(self, item: Any) -> bool:
        """Enqueue item without blocking. Returns False if buffer is full."""
        head = self._head
        tail = self._tail
        if (head - tail) >= self.capacity:
            return False
        self._buffer[head & self.mask] = item
        self._head = head + 1
        return True

    def poll(self) -> Any | None:
        """Dequeue next item without blocking. Returns None if buffer is empty."""
        tail = self._tail
        head = self._head
        if tail == head:
            return None
        idx = tail & self.mask
        item = self._buffer[idx]
        self._buffer[idx] = None
        self._tail = tail + 1
        return item

    def qsize(self) -> int:
        return max(0, self._head - self._tail)

    def empty(self) -> bool:
        return self._tail == self._head

    def full(self) -> bool:
        return (self._head - self._tail) >= self.capacity


class ChannelIngressSequencer:
    """Enforces ADR-003: Single-Threaded Exchange Channel Sequencing.

    Tracks feed-level sequence numbers per channel before routing or symbol demuxing.
    Detects true sequence gaps, duplicate packets, out-of-order deliveries, and session resets.
    Guarantees that interleaved multi-instrument streams on a single feed do not cause false gap alarms.
    """

    def __init__(
        self,
        channel_id: str,
        gap_manager: Optional[Any] = None,
        max_jump_limit: int = 1_000_000,
    ) -> None:
        self.channel_id = str(channel_id)
        self.gap_manager = gap_manager
        self.max_jump_limit = max_jump_limit

        self.last_sequence: Optional[int] = None
        self.expected_sequence: Optional[int] = None
        self.current_session_id: Optional[str] = None

        self.total_received: int = 0
        self.in_order_count: int = 0
        self.gaps_detected: int = 0
        self.missing_events_count: int = 0
        self.duplicates_detected: int = 0
        self.out_of_order_count: int = 0
        self.resets_detected: int = 0

    def verify_sequence(self, event: Any) -> Tuple[QualityStatus, Optional[str]]:
        """Verify the channel sequence number of an incoming raw or canonical event.

        Returns (quality_status, reason_string_or_None).
        """
        self.total_received += 1

        seq = getattr(event, "sequence_number", None)
        sess = getattr(event, "session_id", None)
        src = getattr(event, "source", "UNKNOWN")
        inst = getattr(event, "instrument_id", None) or getattr(event, "instrument", "")

        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            if seq is None:
                seq = payload.get("sequence")
            if sess is None:
                sess = payload.get("session_id")
            if not inst:
                inst = payload.get("instrument") or payload.get("symbol") or ""

        if seq is None:
            return QualityStatus.VALID, None

        sess_str = str(sess) if sess is not None else "SESSION_0"

        # Check for upstream session reset
        if self.current_session_id is not None and sess_str != self.current_session_id:
            self.resets_detected += 1
            self.current_session_id = sess_str
            self.last_sequence = seq
            self.expected_sequence = seq + 1
            self.in_order_count += 1
            return QualityStatus.VALID, None

        if self.current_session_id is None:
            self.current_session_id = sess_str

        # Initial sequence anchor
        if self.expected_sequence is None:
            self.last_sequence = seq
            self.expected_sequence = seq + 1
            self.in_order_count += 1
            return QualityStatus.VALID, None

        # Case 1: In-order progression
        if seq == self.expected_sequence:
            self.last_sequence = seq
            self.expected_sequence = seq + 1
            self.in_order_count += 1
            return QualityStatus.VALID, None

        # Case 2: Duplicate sequence
        if seq == self.last_sequence:
            self.duplicates_detected += 1
            return QualityStatus.SUSPICIOUS, Reason.DUPLICATE.value

        # Case 3: Out-of-order sequence (older than expected)
        if seq < self.expected_sequence:
            self.out_of_order_count += 1
            if self.last_sequence is not None and (self.last_sequence - seq) < 100:
                self.duplicates_detected += 1
                return QualityStatus.SUSPICIOUS, Reason.DUPLICATE.value
            return QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER.value

        # Case 4: Sequence gap (seq > expected_sequence)
        gap_size = seq - self.expected_sequence
        self.gaps_detected += 1
        self.missing_events_count += gap_size

        if self.gap_manager is not None:
            self.gap_manager.record_gap(
                source=src,
                instrument=str(inst),
                expected_start=self.expected_sequence,
                received_seq=seq,
                epoch=self.current_session_id,
            )

        self.last_sequence = seq
        self.expected_sequence = seq + 1
        return QualityStatus.SUSPICIOUS, Reason.SEQUENCE_GAP.value


class SymbolPartitioner:
    """Maps financial instrument tickers to deterministic shard instances.

    Supports both alphabetical range partitioning and uniform CRC32 hash partitioning.
    """

    def __init__(
        self,
        num_shards: int = 2,
        mode: str = "range",
        shard_ranges: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        if num_shards <= 0:
            raise ValueError(f"num_shards must be positive, got {num_shards}")
        self.num_shards = num_shards
        self.mode = mode.lower()
        self.shard_ranges = shard_ranges or []

        if self.mode == "range" and not self.shard_ranges:
            # Default 2-shard split: A-L (Shard 0) and M-Z (Shard 1)
            if self.num_shards == 2:
                self.shard_ranges = [("A", "L"), ("M", "Z")]
            elif self.num_shards == 4:
                self.shard_ranges = [("A", "F"), ("G", "L"), ("M", "R"), ("S", "Z")]
            else:
                # Uniform chunking of alphabet
                alphabet = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
                chunk_size = (len(alphabet) + num_shards - 1) // num_shards
                self.shard_ranges = [
                    (
                        alphabet[i * chunk_size],
                        alphabet[min((i + 1) * chunk_size - 1, len(alphabet) - 1)],
                    )
                    for i in range(num_shards)
                ]

    def get_shard_id(self, instrument: str) -> int:
        """Return the target shard ID for a given instrument symbol."""
        if not instrument:
            return 0
        sym = instrument.strip().upper()
        if self.mode == "hash":
            return zlib.crc32(sym.encode("utf-8")) % self.num_shards

        # Range-based lookup
        first_char = sym[0]
        for idx, (start, end) in enumerate(self.shard_ranges):
            if start <= first_char <= end:
                return idx
        # Fallback to last shard if outside defined ranges (e.g. numeric symbols)
        return self.num_shards - 1


@dataclasses.dataclass
class ConsumerSession:
    """Represents an active downstream consumer subscription."""

    consumer_id: str
    tenant_id: str
    stream_queue: queue.Queue
    subscribed_symbols: Optional[Set[str]] = None
    connected_at: float = dataclasses.field(default_factory=time.time)
    frames_dispatched: int = 0
    frames_dropped: int = 0
    is_active: bool = True


class ConsumerFanoutManager:
    """Manages independent, bounded fan-out queues for downstream consumers.

    Guarantees that a slow or stalled consumer cannot block upstream ingestion
    or degrade peer consumers. Implements automated noisy-neighbor eviction.
    """

    def __init__(
        self, max_buffer_per_client: int = 5000, eviction_timeout_sec: float = 0.05
    ) -> None:
        self.max_buffer_per_client = max_buffer_per_client
        self.eviction_timeout_sec = eviction_timeout_sec
        self._lock = threading.Lock()
        self._consumers: Dict[str, ConsumerSession] = {}
        self._total_dispatched: int = 0
        self._total_evictions: int = 0

    def register_consumer(
        self,
        consumer_id: str,
        tenant_id: str,
        subscribed_symbols: Optional[List[str]] = None,
    ) -> ConsumerSession:
        """Register a new consumer with an independent bounded dispatch queue."""
        with self._lock:
            symbols_set = (
                set(s.upper() for s in subscribed_symbols)
                if subscribed_symbols
                else None
            )
            session = ConsumerSession(
                consumer_id=consumer_id,
                tenant_id=tenant_id,
                stream_queue=queue.Queue(maxsize=self.max_buffer_per_client),
                subscribed_symbols=symbols_set,
            )
            self._consumers[consumer_id] = session
            return session

    def unregister_consumer(self, consumer_id: str) -> bool:
        """Remove and close a consumer session."""
        with self._lock:
            session = self._consumers.pop(consumer_id, None)
            if session:
                session.is_active = False
                return True
            return False

    def broadcast_event(self, symbol: str, frame: bytes) -> int:
        """Broadcast a binary SBE frame to all entitled, healthy consumers.

        Non-blocking: if a consumer queue is full, the frame is dropped for that
        specific consumer and an eviction warning is recorded without stalling others.
        """
        sym = symbol.upper()
        dispatched_count = 0
        to_evict: List[str] = []

        with self._lock:
            sessions = list(self._consumers.values())

        for session in sessions:
            if not session.is_active:
                continue
            if (
                session.subscribed_symbols is not None
                and sym not in session.subscribed_symbols
            ):
                continue

            try:
                session.stream_queue.put_nowait((sym, frame))
                session.frames_dispatched += 1
                dispatched_count += 1
                self._total_dispatched += 1
            except queue.Full:
                session.frames_dropped += 1
                # If drops exceed threshold, mark for disconnection
                if session.frames_dropped >= 10:
                    to_evict.append(session.consumer_id)

        if to_evict:
            with self._lock:
                for cid in to_evict:
                    if cid in self._consumers:
                        self._consumers[cid].is_active = False
                        del self._consumers[cid]
                        self._total_evictions += 1

        return dispatched_count

    def stats(self) -> Dict[str, Any]:
        """Return operational fan-out statistics."""
        with self._lock:
            active_count = len(self._consumers)
            client_stats = {
                cid: {
                    "tenant_id": s.tenant_id,
                    "dispatched": s.frames_dispatched,
                    "dropped": s.frames_dropped,
                    "queue_depth": s.stream_queue.qsize(),
                }
                for cid, s in self._consumers.items()
            }
        return {
            "active_consumers": active_count,
            "total_dispatched": self._total_dispatched,
            "total_evictions": self._total_evictions,
            "clients": client_stats,
        }


class TenantQuotaManager:
    """Enforces multi-tenant resource boundaries, rate limits, and subscription quotas."""

    def __init__(
        self,
        default_max_subscriptions: int = 100,
        default_max_rate_eps: int = 5000,
    ) -> None:
        self.default_max_subscriptions = default_max_subscriptions
        self.default_max_rate_eps = default_max_rate_eps
        self._tenant_quotas: Dict[str, Dict[str, int]] = {}
        self._tenant_rates: Dict[str, collections.deque] = collections.defaultdict(
            collections.deque
        )
        self._lock = threading.Lock()

    def set_quota(
        self, tenant_id: str, max_subscriptions: int, max_rate_eps: int
    ) -> None:
        """Configure explicit quota limits for a tenant."""
        with self._lock:
            self._tenant_quotas[tenant_id] = {
                "max_subscriptions": max_subscriptions,
                "max_rate_eps": max_rate_eps,
            }

    def check_subscription_permitted(
        self, tenant_id: str, requested_symbol_count: int
    ) -> bool:
        """Verify whether tenant is permitted to subscribe to the requested symbol count."""
        with self._lock:
            quota = self._tenant_quotas.get(tenant_id, {})
            max_sub = quota.get("max_subscriptions", self.default_max_subscriptions)
            return requested_symbol_count <= max_sub

    def record_and_check_rate(self, tenant_id: str, count: int = 1) -> bool:
        """Sliding-window token rate limiter (1-second window).

        Returns True if permitted, False if tenant exceeded rate quota.
        """
        now = time.time()
        window_start = now - 1.0

        with self._lock:
            quota = self._tenant_quotas.get(tenant_id, {})
            max_eps = quota.get("max_rate_eps", self.default_max_rate_eps)
            q = self._tenant_rates[tenant_id]

            # Prune old timestamps
            while q and q[0] < window_start:
                q.popleft()

            if len(q) + count > max_eps:
                return False  # Rate limit exceeded

            for _ in range(count):
                q.append(now)
            return True


class ShardInstance:
    """Represents an active, isolated MDRAP shard processing its assigned symbol universe."""

    def __init__(self, config: ShardConfig, use_spsc: bool = False) -> None:
        self.config = config
        self.use_spsc = use_spsc
        if use_spsc:
            self.in_queue = SpscRingQueue(capacity=config.max_queue_depth)
        else:
            self.in_queue = queue.Queue(maxsize=config.max_queue_depth)
        self.fanout = ConsumerFanoutManager()
        self.sequence_number: int = 0
        self.total_processed: int = 0
        self.is_running: bool = False
        self._worker_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the shard worker thread."""
        self.is_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop the shard worker thread and drain queue."""
        self.is_running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

    def enqueue_event(self, symbol: str, payload: bytes) -> bool:
        """Push an incoming event to the shard queue. Returns False if queue full."""
        if self.use_spsc:
            return self.in_queue.offer((symbol, payload))
        try:
            self.in_queue.put_nowait((symbol, payload))
            return True
        except queue.Full:
            return False

    def _worker_loop(self) -> None:
        """Hot processing loop for the shard."""
        while self.is_running:
            if self.use_spsc:
                item = self.in_queue.poll()
                if item is None:
                    time.sleep(0.0005)
                    continue
                symbol, payload = item
            else:
                try:
                    symbol, payload = self.in_queue.get(timeout=0.01)
                except queue.Empty:
                    continue

            self.sequence_number += 1
            self.total_processed += 1

            # Dispatch to downstream consumers
            self.fanout.broadcast_event(symbol, payload)
            if not self.use_spsc:
                self.in_queue.task_done()


class FleetCoordinator:
    """Coordinates multiple independent MDRAP shards into a unified operational fleet.

    Maintains partition routing, fleet health metrics, and overall ingestion dispatch.
    """

    def __init__(
        self,
        partitioner: SymbolPartitioner,
        shard_configs: List[ShardConfig],
        use_spsc: bool = False,
    ) -> None:
        self.partitioner = partitioner
        self.use_spsc = use_spsc
        self.shards: Dict[int, ShardInstance] = {
            cfg.shard_id: ShardInstance(cfg, use_spsc=use_spsc) for cfg in shard_configs
        }
        self.quota_manager = TenantQuotaManager()

    def start_fleet(self) -> None:
        """Start all configured shard instances."""
        for shard in self.shards.values():
            shard.start()

    def stop_fleet(self) -> None:
        """Stop all configured shard instances cleanly."""
        for shard in self.shards.values():
            shard.stop()

    def dispatch_event(self, instrument: str, payload: bytes) -> Tuple[int, bool]:
        """Route an event to the authoritative shard. Returns (shard_id, success)."""
        shard_id = self.partitioner.get_shard_id(instrument)
        shard = self.shards.get(shard_id)
        if not shard:
            return shard_id, False
        success = shard.enqueue_event(instrument, payload)
        return shard_id, success

    def fleet_health(self) -> Dict[str, Any]:
        """Aggregate health and throughput metrics across all shards."""
        total_processed = 0
        total_backlog = 0
        total_consumers = 0
        shard_summaries = {}

        for sid, shard in self.shards.items():
            total_processed += shard.total_processed
            qsize = shard.in_queue.qsize()
            total_backlog += qsize
            f_stats = shard.fanout.stats()
            total_consumers += f_stats["active_consumers"]

            shard_summaries[f"shard_{sid}"] = {
                "name": shard.config.name,
                "is_running": shard.is_running,
                "sequence_head": shard.sequence_number,
                "total_processed": shard.total_processed,
                "queue_depth": qsize,
                "active_consumers": f_stats["active_consumers"],
                "evictions": f_stats["total_evictions"],
            }

        return {
            "fleet_status": "HEALTHY"
            if all(s.is_running for s in self.shards.values())
            else "DEGRADED",
            "total_shards": len(self.shards),
            "aggregate_events_processed": total_processed,
            "aggregate_queue_backlog": total_backlog,
            "aggregate_consumers": total_consumers,
            "shards": shard_summaries,
        }
