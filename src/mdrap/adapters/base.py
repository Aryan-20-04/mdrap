"""Base Feed Adapter Infrastructure for MDRAP (Phase 4).

Provides standardized sequence tracking, exponential backoff reconnection with jitter,
bounded memory buffering with explicit drop counters, capability declarations,
and health telemetry for public and institutional exchange feed adapters.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from enum import Enum
import logging
import queue
import random
import threading
import time
from typing import Any, Iterator, Optional

from ..models import CanonicalEvent, EventType, QualityStatus, RawEvent

logger = logging.getLogger("mdrap.adapters")
__stability__ = "stable"


class AdapterStreamState(str, Enum):
    """Lifecycle state machine for feed adapters."""

    INITIALIZED = "INITIALIZED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"  # Normal healthy streaming
    DEGRADED = "DEGRADED"  # Sequence gap, backpressure, or reconnection in progress
    RECONNECTING = "RECONNECTING"  # Backing off and retrying connection
    DISCONNECTED = "DISCONNECTED"  # Stopped or unrecoverably disconnected


class LicensingTier(str, Enum):
    """Regulatory and commercial market-data licensing boundaries."""

    PUBLIC_COMMUNITY = (
        "PUBLIC_COMMUNITY"  # Free public endpoints (Binance/Kraken public WS)
    )
    COMMERCIAL_INTERNAL_DISPLAY = (
        "COMMERCIAL_INTERNAL_DISPLAY"  # Internal trading and monitoring only
    )
    NON_DISPLAY_PROP_TRADING = (
        "NON_DISPLAY_PROP_TRADING"  # Algorithmic and proprietary execution
    )
    REDISTRIBUTION_RESTRICTED = (
        "REDISTRIBUTION_RESTRICTED"  # Redistribution prohibited without vendor license
    )


@dataclass(frozen=True)
class AdapterCapabilities:
    """Declared capabilities, sequence guarantees, and licensing rules for a feed adapter."""

    venue: str
    supports_monotonic_sequence: bool = True
    supports_gap_detection: bool = True
    supports_retransmission: bool = False
    supports_snapshots: bool = True
    supports_historical_replay: bool = False
    licensing_tier: LicensingTier = LicensingTier.PUBLIC_COMMUNITY
    clock_source: str = "EXCHANGE"  # "EXCHANGE", "GATEWAY_RECV", "HYBRID"
    transport: str = "WEBSOCKET"  # "WEBSOCKET", "TCP_SOCKET", "BINARY_FILE"
    max_burst_eps: int = 50_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "supports_monotonic_sequence": self.supports_monotonic_sequence,
            "supports_gap_detection": self.supports_gap_detection,
            "supports_retransmission": self.supports_retransmission,
            "supports_snapshots": self.supports_snapshots,
            "supports_historical_replay": self.supports_historical_replay,
            "licensing_tier": self.licensing_tier.value,
            "clock_source": self.clock_source,
            "transport": self.transport,
            "max_burst_eps": self.max_burst_eps,
        }


class SequenceTracker:
    """Monotonic sequence tracking, gap detection, and retransmission accounting."""

    def __init__(self, venue: str = "", strict: bool = True) -> None:
        self.venue = venue
        self.strict = strict
        self.last_sequence: Optional[int] = None
        self.gaps_total: int = 0
        self.missing_frames_total: int = 0
        self.duplicates_total: int = 0
        self.out_of_order_total: int = 0
        self.gap_history: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=100
        )

    def observe(self, seq: Optional[int]) -> tuple[bool, Optional[str]]:
        """Validate sequence monotonicity against previous observations.

        Returns (is_monotonic, anomaly_reason).
        """
        if seq is None:
            if self.strict:
                return False, "missing_sequence_number"
            return True, None

        if not isinstance(seq, int):
            return False, f"invalid_sequence_type_{type(seq).__name__}"

        if self.last_sequence is None:
            self.last_sequence = seq
            return True, None

        if seq == self.last_sequence:
            self.duplicates_total += 1
            return False, f"duplicate_sequence_{seq}"

        if seq < self.last_sequence:
            self.out_of_order_total += 1
            return False, f"out_of_order_sequence_{seq}_after_{self.last_sequence}"

        # Monotonic advance check: gap detection
        expected = self.last_sequence + 1
        if seq > expected:
            gap_size = seq - expected
            self.gaps_total += 1
            self.missing_frames_total += gap_size
            incident = {
                "timestamp": time.time(),
                "expected": expected,
                "received": seq,
                "gap_size": gap_size,
            }
            self.gap_history.append(incident)
            logger.warning(
                "[%s] Sequence GAP detected: expected %d, received %d (lost %d frames)",
                self.venue or "adapter",
                expected,
                seq,
                gap_size,
            )
            self.last_sequence = seq
            return False, f"sequence_gap_{gap_size}_frames"

        # Perfectly monotonic
        self.last_sequence = seq
        return True, None

    def reset(self) -> None:
        """Reset sequence tracking state upon reconnection or channel reset."""
        self.last_sequence = None


class ReconnectTracker:
    """Exponential backoff with jitter and reconnection telemetry."""

    def __init__(
        self,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
        jitter_ratio: float = 0.2,
    ) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.jitter_ratio = jitter_ratio
        self.attempts: int = 0
        self.reconnects_total: int = 0
        self.last_attempt_time: float = 0.0

    def next_backoff(self) -> float:
        """Calculate next sleep interval using exponential backoff with jitter."""
        self.attempts += 1
        self.reconnects_total += 1
        self.last_attempt_time = time.time()

        delay = min(
            self.max_delay,
            self.base_delay * (self.backoff_factor ** (self.attempts - 1)),
        )
        jitter = delay * self.jitter_ratio * random.uniform(-1.0, 1.0)
        final_delay = min(self.max_delay, max(0.01, delay + jitter))
        return round(final_delay, 3)

    def record_success(self) -> None:
        """Reset consecutive attempt counter upon successful connection."""
        self.attempts = 0


class BaseFeedAdapter:
    """Institutional base class for all MDRAP feed adapters.

    Provides:
    - Thread-safe bounded receive queue with drop accounting.
    - Monotonic sequence tracking with gap detection.
    - Exponential backoff reconnect management with jitter.
    - Full FeedAdapter protocol conformance (`connect`, `disconnect`, `receive`, `normalize`, `health`, `capabilities`).
    - Context manager and iterator support.
    """

    def __init__(
        self,
        venue: str,
        symbol: str,
        max_buffer_size: int = 10_000,
        strict_sequence: bool = True,
    ) -> None:
        self.venue = venue
        self.symbol = symbol
        self.max_buffer_size = max_buffer_size

        self.state = AdapterStreamState.INITIALIZED
        self.seq_tracker = SequenceTracker(venue=venue, strict=strict_sequence)
        self.reconnect_tracker = ReconnectTracker()

        self._buffer: queue.Queue[RawEvent] = queue.Queue(maxsize=max_buffer_size)
        self._received_total: int = 0
        self._drops_total: int = 0
        self._connected_since: float = 0.0
        self._last_receive_time: float = 0.0
        self._lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        return self.state in (AdapterStreamState.CONNECTED, AdapterStreamState.DEGRADED)

    def connect(self) -> None:
        """Establish connection to upstream feed."""
        with self._lock:
            self.state = AdapterStreamState.CONNECTING
            try:
                self._do_connect()
                self.state = AdapterStreamState.CONNECTED
                self._connected_since = time.time()
                self.reconnect_tracker.record_success()
                logger.info(
                    "[%s] Adapter connected for symbol %s", self.venue, self.symbol
                )
            except Exception as exc:
                self.state = AdapterStreamState.RECONNECTING
                logger.warning("[%s] Failed to connect: %s", self.venue, exc)
                raise

    def disconnect(self) -> None:
        """Terminate connection and release resources."""
        with self._lock:
            try:
                self._do_disconnect()
            finally:
                self.state = AdapterStreamState.DISCONNECTED
                self._connected_since = 0.0
                logger.info("[%s] Adapter disconnected", self.venue)

    def enqueue_raw(self, raw: RawEvent) -> bool:
        """Enqueue an incoming raw packet into bounded buffer.

        If buffer is full, evicts oldest packet to maintain real-time freshness
        and increments drops_total (Audit Phase 4). Never silently drops!
        """
        try:
            self._buffer.put_nowait(raw)
            with self._lock:
                self._received_total += 1
                self._last_receive_time = time.time()
            return True
        except queue.Full:
            # Drop oldest frame with explicit drop tracking
            try:
                self._buffer.get_nowait()
            except queue.Empty:
                pass
            try:
                self._buffer.put_nowait(raw)
            except queue.Full:
                pass

            with self._lock:
                self._drops_total += 1
                self._received_total += 1
                self._last_receive_time = time.time()
                self.state = AdapterStreamState.DEGRADED
                if self._drops_total % 100 == 1:
                    logger.warning(
                        "[%s] Backpressure drop count: %d frames dropped on %s",
                        self.venue,
                        self._drops_total,
                        self.symbol,
                    )
            return False

    def receive(self, timeout: Optional[float] = 0.0) -> Optional[RawEvent]:
        """Fetch next raw packet from receive queue."""
        if self.state == AdapterStreamState.DISCONNECTED:
            return None
        try:
            if timeout and timeout > 0:
                return self._buffer.get(timeout=timeout)
            return self._buffer.get_nowait()
        except queue.Empty:
            return None

    def feed_simulated_packet(
        self,
        seq: Optional[int],
        price: Optional[float] = None,
        qty: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        ex_ts: Optional[float] = None,
        event_type: str = "TRADE",
    ) -> RawEvent:
        """Helper to inject simulated packets directly for unit testing and offline replay."""
        now = time.time()
        payload: dict[str, Any] = {
            "instrument": self.symbol,
            "event_type": event_type,
            "sequence": seq,
            "exchange_ts": ex_ts or now,
        }
        if price is not None:
            payload["price"] = price
        if qty is not None:
            payload["quantity"] = qty
        if bid is not None:
            payload["bid"] = bid
        if ask is not None:
            payload["ask"] = ask

        raw = RawEvent(
            source=self.venue,
            payload=payload,
            receive_timestamp=now,
            raw_id=f"{self.venue.lower()}_{seq or 0}_{int(now * 1000)}",
        )
        self.enqueue_raw(raw)
        return raw

    def health(self) -> dict[str, Any]:
        """Return connectivity, sequence gaps, drops, and stream diagnostics."""
        with self._lock:
            uptime = (
                max(0.0, time.time() - self._connected_since)
                if self._connected_since
                else 0.0
            )
            return {
                "venue": self.venue,
                "symbol": self.symbol,
                "state": self.state.value,
                "connected": self.is_connected,
                "status": "HEALTHY"
                if self.state == AdapterStreamState.CONNECTED
                else self.state.value,
                "received_total": self._received_total,
                "drops_total": self._drops_total,
                "gaps_total": self.seq_tracker.gaps_total,
                "missing_frames_total": self.seq_tracker.missing_frames_total,
                "duplicates_total": self.seq_tracker.duplicates_total,
                "out_of_order_total": self.seq_tracker.out_of_order_total,
                "reconnects_total": self.reconnect_tracker.reconnects_total,
                "buffer_size": self._buffer.qsize(),
                "last_sequence": self.seq_tracker.last_sequence,
                "last_receive_time": self._last_receive_time,
                "uptime_seconds": round(uptime, 2),
            }

    def capabilities(self) -> AdapterCapabilities:
        """Declare adapter capabilities and licensing boundaries. Must be overridden by subclasses."""
        return AdapterCapabilities(venue=self.venue)

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Transform raw venue framing into canonical schema. Must be implemented by subclasses."""
        raise NotImplementedError

    def _do_connect(self) -> None:
        """Subclass implementation of connection logic."""
        pass

    def _do_disconnect(self) -> None:
        """Subclass implementation of disconnection logic."""
        pass

    # Legacy FeedAdapter protocol compatibility aliases
    def open(self) -> None:
        self.connect()

    def close(self) -> None:
        self.disconnect()

    def __iter__(self) -> Iterator[CanonicalEvent]:
        while self.is_connected:
            raw = self.receive(timeout=0.01)
            if raw is not None:
                yield self.normalize(raw)

    def __enter__(self) -> BaseFeedAdapter:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()
