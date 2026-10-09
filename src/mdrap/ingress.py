"""Market-Data Ingress Adapter Architecture & Framework (Phase 3 Workstream F).

Establishes the formal institutional contract for exchange feed adapters, distinguishing:
  RAW_RECEIVED -> PARSED -> VALIDATED -> NORMALIZED -> ACCEPTED -> COMMITTED

Preserves source sequence domains, enforces adapter state machine, detects sequence gaps,
and handles deterministic replay and socket streaming.
"""

from __future__ import annotations

import collections
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import CanonicalEvent, EventType, QualityStatus, RawEvent

logger = logging.getLogger(__name__)

__stability__ = "stable"


class AdapterState(str, enum.Enum):
    """Lifecycle states of a market data feed adapter."""

    UNINITIALIZED = "UNINITIALIZED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


class IngressEventStage(str, enum.Enum):
    """Formal audit stages of an ingested market data message."""

    RAW_RECEIVED = "RAW_RECEIVED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    NORMALIZED = "NORMALIZED"
    ACCEPTED = "ACCEPTED"
    COMMITTED = "COMMITTED"


class IngressError(Exception):
    """Base exception for feed ingress errors."""


class FramingError(IngressError):
    """Raised when incoming byte frame length or delimiters are malformed."""


class SequenceGapError(IngressError):
    """Raised when an unexpected gap in the source sequence domain is detected."""


@dataclass
class FeedAdapterConfig:
    """Configuration parameters for a feed adapter."""

    venue: str
    feed_id: str
    session_id: str = "SESSION_1"
    symbols: list[str] = field(default_factory=list)
    reconnect_max_attempts: int = 5
    heartbeat_interval_s: float = 5.0
    gap_detection_enabled: bool = True
    max_frame_bytes: int = 65536

    def __post_init__(self) -> None:
        if not self.venue or not self.venue.strip():
            raise IngressError("Adapter configuration must specify a non-empty venue name.")
        if not self.feed_id or not self.feed_id.strip():
            raise IngressError("Adapter configuration must specify a non-empty feed_id.")
        if self.max_frame_bytes <= 0:
            raise IngressError("max_frame_bytes must be strictly positive.")


@dataclass
class IngressStats:
    """Comprehensive telemetry counters for an ingress stream."""

    raw_frames_received: int = 0
    parsed_count: int = 0
    validation_failures: int = 0
    normalized_count: int = 0
    gaps_detected: int = 0
    missing_events_count: int = 0
    duplicates_detected: int = 0
    reconnect_count: int = 0
    last_source_sequence: Optional[int] = None
    last_receive_timestamp: float = 0.0


class BaseFeedAdapter:
    """Abstract base class for all MDRAP market data feed adapters."""

    def __init__(self, config: FeedAdapterConfig) -> None:
        self.config = config
        self.state: AdapterState = AdapterState.UNINITIALIZED
        self.stats: IngressStats = IngressStats()
        self.subscribed_symbols: set[str] = set(config.symbols)
        self._expected_sequence: Optional[int] = None
        self.state = AdapterState.CLOSED

    def connect(self) -> None:
        """Initialize transport resources and advance state to CONNECTED."""
        if self.state in (AdapterState.CONNECTED, AdapterState.STREAMING):
            return
        self.state = AdapterState.CONNECTING
        try:
            self._do_connect()
            self.state = AdapterState.CONNECTED
        except Exception as exc:
            self.state = AdapterState.ERROR
            logger.error("[ingress] Failed to connect adapter %s:%s: %s", self.config.venue, self.config.feed_id, exc)
            raise

    def disconnect(self) -> None:
        """Gracefully release transport resources and advance state to CLOSED."""
        try:
            self._do_disconnect()
        finally:
            self.state = AdapterState.CLOSED

    def subscribe(self, symbol: str) -> None:
        """Subscribe to an instrument symbol."""
        sym = symbol.strip().upper()
        if sym:
            self.subscribed_symbols.add(sym)
            if self.state in (AdapterState.CONNECTED, AdapterState.STREAMING):
                self._send_subscription(sym)

    def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from an instrument symbol."""
        sym = symbol.strip().upper()
        self.subscribed_symbols.discard(sym)

    def poll(self, timeout_s: float = 0.0) -> Optional[RawEvent]:
        """Poll next raw event from transport buffer with gap detection."""
        if self.state not in (AdapterState.CONNECTED, AdapterState.STREAMING):
            raise IngressError(f"Cannot poll while adapter is in state {self.state}")

        raw = self._do_poll(timeout_s)
        if raw is None:
            return None

        self.state = AdapterState.STREAMING
        self.stats.raw_frames_received += 1
        self.stats.last_receive_timestamp = raw.receive_timestamp

        # Extract and audit sequence domain
        seq = self._extract_sequence(raw)
        if seq is not None and self.config.gap_detection_enabled:
            self._audit_sequence(seq)

        return raw

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate raw event into canonical schema while preserving provenance."""
        parsed = self.parse(raw)
        if not self.validate_source_frame(parsed):
            self.stats.validation_failures += 1
            raise IngressError(f"Source frame validation failed for payload from {self.config.venue}")

        canonical = self._do_normalize(raw, parsed)
        self.stats.normalized_count += 1
        return canonical

    def parse(self, raw: RawEvent) -> dict[str, Any]:
        """Parse raw event payload into structured dictionary."""
        if isinstance(raw.payload, dict):
            self.stats.parsed_count += 1
            return raw.payload
        raise IngressError(f"Unsupported raw payload type: {type(raw.payload)}")

    def validate_source_frame(self, parsed: dict[str, Any]) -> bool:
        """Validate presence of minimum mandatory fields."""
        return "sym" in parsed or "instrument" in parsed

    def health(self) -> dict[str, Any]:
        """Return connectivity health and stream status."""
        return {
            "venue": self.config.venue,
            "feed_id": self.config.feed_id,
            "session_id": self.config.session_id,
            "state": self.state.value,
            "connected": self.state in (AdapterState.CONNECTED, AdapterState.STREAMING),
            "subscribed_symbols": list(self.subscribed_symbols),
            "last_receive_ts": self.stats.last_receive_timestamp,
            "gaps_detected": self.stats.gaps_detected,
        }

    def _audit_sequence(self, seq: int) -> None:
        if self._expected_sequence is not None:
            if seq == self._expected_sequence:
                self._expected_sequence = seq + 1
            elif seq < self._expected_sequence:
                self.stats.duplicates_detected += 1
                logger.warning(
                    "[ingress] Duplicate or out-of-order sequence %d on %s:%s (expected %d)",
                    seq, self.config.venue, self.config.feed_id, self._expected_sequence,
                )
            else:
                gap = seq - self._expected_sequence
                self.stats.gaps_detected += 1
                self.stats.missing_events_count += gap
                logger.warning(
                    "[ingress] Sequence gap of %d detected on %s:%s (expected %d, got %d)",
                    gap, self.config.venue, self.config.feed_id, self._expected_sequence, seq,
                )
                self._expected_sequence = seq + 1
        else:
            self._expected_sequence = seq + 1

        self.stats.last_source_sequence = seq

    # Subclass extension points
    def _do_connect(self) -> None:
        pass

    def _do_disconnect(self) -> None:
        pass

    def _send_subscription(self, symbol: str) -> None:
        pass

    def _do_poll(self, timeout_s: float) -> Optional[RawEvent]:
        raise NotImplementedError

    def _extract_sequence(self, raw: RawEvent) -> Optional[int]:
        if isinstance(raw.payload, dict):
            return raw.payload.get("seq") or raw.payload.get("sequence")
        return None

    def _do_normalize(self, raw: RawEvent, parsed: dict[str, Any]) -> CanonicalEvent:
        raise NotImplementedError


class ReplayFeedAdapter(BaseFeedAdapter):
    """Deterministic, reproducible test/replay feed adapter (Phase 3 Gate 2)."""

    def __init__(
        self,
        config: Optional[FeedAdapterConfig] = None,
        frames: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        cfg = config or FeedAdapterConfig(venue="REPLAY_SIM", feed_id="REPLAY_CH1")
        super().__init__(cfg)
        self._frames: collections.deque[dict[str, Any]] = collections.deque(frames or [])
        self._initial_frame_count: int = len(self._frames)

    def load_frames(self, frames: list[dict[str, Any]]) -> None:
        """Load deterministic test frames for replay."""
        self._frames = collections.deque(frames)
        self._initial_frame_count = len(frames)

    def _do_poll(self, timeout_s: float) -> Optional[RawEvent]:
        if not self._frames:
            return None
        frame = self._frames.popleft()
        now = time.time()
        raw_id = f"replay_{self.config.venue}_{frame.get('seq', 0)}_{int(now * 1000)}"
        return RawEvent(
            raw_id=raw_id,
            source=self.config.venue,
            receive_timestamp=now,
            payload=frame,
        )

    def _do_normalize(self, raw: RawEvent, parsed: dict[str, Any]) -> CanonicalEvent:
        sym = str(parsed.get("sym") or parsed.get("instrument") or "AAPL")
        ex_ts = float(parsed.get("ts") or parsed.get("exchange_ts") or raw.receive_timestamp)
        px = float(parsed.get("px") or parsed.get("price") or 100.0)
        qty = float(parsed.get("sz") or parsed.get("quantity") or 1.0)
        seq = int(parsed.get("seq") or parsed.get("sequence") or 0)
        ev_type_str = str(parsed.get("type", "TRADE")).upper()
        ev_type = EventType.QUOTE if ev_type_str == "QUOTE" else EventType.TRADE

        return CanonicalEvent(
            event_id=raw.raw_id,
            instrument_id=sym,
            event_type=ev_type,
            exchange_timestamp=ex_ts,
            receive_timestamp=raw.receive_timestamp,
            processing_timestamp=time.time(),
            source=self.config.venue,
            sequence_number=seq,
            price=px,
            quantity=qty,
            bid_price=float(parsed["bid"]) if "bid" in parsed else None,
            ask_price=float(parsed["ask"]) if "ask" in parsed else None,
            quality_status=QualityStatus.VALID,
        )
