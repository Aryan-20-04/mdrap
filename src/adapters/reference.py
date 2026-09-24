"""Reference FeedAdapter Implementation.

Demonstrates complete compliance with the MDRAP FeedAdapter protocol:
- connect()
- disconnect()
- receive()
- normalize()
- health()
"""

from __future__ import annotations

import collections
import time
from typing import Any, Optional

from models import CanonicalEvent, EventType, QualityStatus, RawEvent

__stability__ = "stable"


class ReferenceFeedAdapter:
    """Production-grade reference implementation of FeedAdapter."""

    def __init__(self, source_name: str = "REF_EXCHANGE", symbol: str = "AAPL"):
        self.source_name = source_name
        self.symbol = symbol
        self._connected = False
        self._buffer: collections.deque[RawEvent] = collections.deque()
        self._received_count = 0
        self._last_receive_time = 0.0
        self._sim_seq = 0

    def connect(self) -> None:
        """Establish simulated feed connection and prepare buffers."""
        self._connected = True
        self._last_receive_time = time.time()

    def disconnect(self) -> None:
        """Gracefully terminate connection and drain buffers."""
        self._connected = False
        self._buffer.clear()

    def feed_simulated_packet(
        self,
        seq: int,
        price: float,
        qty: float,
        side: str = "BUY",
        ex_ts: Optional[float] = None,
    ) -> None:
        """Helper to inject simulated packets into the internal receive buffer."""
        now = time.time()
        raw = RawEvent(
            raw_id=f"{self.source_name}_{seq}_{int(now * 1000)}",
            source=self.source_name,
            receive_timestamp=now,
            payload={
                "msg_type": "TRADE",
                "seq": seq,
                "sym": self.symbol,
                "px": price,
                "sz": qty,
                "side": side,
                "ts": ex_ts or now - 0.001,
            },
        )
        self._buffer.append(raw)

    def receive(self) -> RawEvent | None:
        """Fetch next raw packet from the buffer."""
        if not self._connected:
            raise ConnectionError(
                f"Feed adapter {self.source_name} is not connected. Call connect() first."
            )

        if self._buffer:
            raw = self._buffer.popleft()
            self._received_count += 1
            self._last_receive_time = time.time()
            return raw
        return None

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Normalize venue-specific raw JSON framing into a typed CanonicalEvent."""
        payload: dict[str, Any] = raw.payload if isinstance(raw.payload, dict) else {}
        ex_ts = float(payload.get("ts", raw.receive_timestamp))
        px = float(payload.get("px", 0.0))
        qty = float(payload.get("sz", 0.0))
        seq = int(payload.get("seq", 0))
        sym = str(payload.get("sym", self.symbol))

        return CanonicalEvent(
            event_id=raw.raw_id,
            instrument_id=sym,
            event_type=EventType.TRADE,
            exchange_timestamp=ex_ts,
            receive_timestamp=raw.receive_timestamp,
            processing_timestamp=time.time(),
            source=self.source_name,
            sequence_number=seq,
            venue=self.source_name,
            price=px,
            quantity=qty,
            quality_status=QualityStatus.VALID,
            reasons=[],
        )

    def health(self) -> dict[str, Any]:
        """Diagnostic telemetry on connection state, latency, and throughput."""
        now = time.time()
        return {
            "source": self.source_name,
            "connected": self._connected,
            "buffer_depth": len(self._buffer),
            "total_received": self._received_count,
            "last_seen_seconds_ago": round(now - self._last_receive_time, 4)
            if self._last_receive_time > 0
            else None,
            "status": "HEALTHY"
            if self._connected and (now - self._last_receive_time < 2.0)
            else "DEGRADED",
        }

    # Backward compatibility
    def open(self) -> None:
        self.connect()

    def close(self) -> None:
        self.disconnect()
