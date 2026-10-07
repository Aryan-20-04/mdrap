"""MDRAP Institutional Feed Recovery State Machine & Gap Healing Engine (Spec §18, Phase 21).

Implements the institutional feed recovery lifecycle:
    DISCONNECTED
        │
    CONNECTING
        │
     SNAPSHOT
        │
       LIVE ◄──────────────┐
        │                  │
   GAP_DETECTED            │
        │                  │
    RECOVERING ────────────┘
        │
  FAILED_RECOVERY

Guarantees:
1. Determinism: No sequence gap can silently transition to normal live processing.
2. Stale Snapshot Protection: Outdated historical snapshots are detected and rejected.
3. Bounded Buffering: Out-of-order packets are held during backfill and drained in strictly monotonic order.
4. Fast Dedup: Out-of-order and duplicate recovery packets are discarded in O(1) time.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable

__stability__ = "stable"

logger = logging.getLogger("mdrap.recovery")


class FeedState(str, Enum):
    """Institutional Market Data Feed Connection & Recovery States."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    SNAPSHOT = "SNAPSHOT"
    LIVE = "LIVE"
    GAP_DETECTED = "GAP_DETECTED"
    RECOVERING = "RECOVERING"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    FAILED_RECOVERY = "FAILED_RECOVERY"


@dataclass(slots=True)
class FeedPacket:
    """Standardized market data sequence container for feed recovery."""

    channel_id: str
    sequence_num: int
    payload: bytes
    timestamp: float
    is_replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "seq": self.sequence_num,
            "size": len(self.payload),
            "timestamp": self.timestamp,
            "is_replayed": self.is_replayed,
        }


@dataclass
class RecoveryMetrics:
    """Operational metrics for feed state machine and recovery health."""

    state_transitions: int = 0
    gaps_detected: int = 0
    replays_requested: int = 0
    replays_completed: int = 0
    recovery_failures: int = 0
    stale_snapshots_rejected: int = 0
    dedup_dropped: int = 0
    in_order_dispatched: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "state_transitions": self.state_transitions,
            "gaps_detected": self.gaps_detected,
            "replays_requested": self.replays_requested,
            "replays_completed": self.replays_completed,
            "recovery_failures": self.recovery_failures,
            "stale_snapshots_rejected": self.stale_snapshots_rejected,
            "dedup_dropped": self.dedup_dropped,
            "in_order_dispatched": self.in_order_dispatched,
        }


class FeedRecoveryEngine:
    """Deterministic Feed Recovery State Machine & Resequencing Engine."""

    def __init__(
        self,
        channel_id: str = "DEFAULT",
        initial_seq: int = 1,
        max_buffer_size: int = 10000,
        replay_handler: Callable[[str, int, int], list[FeedPacket]] | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.expected_seq = initial_seq
        self.max_buffer_size = max_buffer_size
        self.replay_handler = replay_handler

        self._state: FeedState = FeedState.DISCONNECTED
        self._lock = threading.RLock()
        self._last_state_change = time.time()
        self._last_processed_ts = 0.0

        # Gap buffer: sequence_num -> FeedPacket
        self._gap_buffer: dict[int, FeedPacket] = collections.defaultdict(dict)

        self.metrics = RecoveryMetrics()

    @property
    def state(self) -> FeedState:
        with self._lock:
            return self._state

    def _set_state(self, new_state: FeedState, reason: str = "") -> None:
        if self._state != new_state:
            prev = self._state
            self._state = new_state
            self._last_state_change = time.time()
            self.metrics.state_transitions += 1
            logger.info(
                "[%s] Feed state transition: %s -> %s (%s)",
                self.channel_id,
                prev.value,
                new_state.value,
                reason,
            )

    def connect(self, require_snapshot: bool = True) -> None:
        """Initiate connection lifecycle."""
        with self._lock:
            self._set_state(FeedState.CONNECTING, "Connect initiated")
            if require_snapshot:
                self._set_state(FeedState.SNAPSHOT, "Awaiting initial book snapshot")
            else:
                self._set_state(FeedState.LIVE, "Direct live mode enabled")

    def disconnect(self, reason: str = "Manual disconnect") -> None:
        """Terminate connection and transition to DISCONNECTED."""
        with self._lock:
            self._set_state(FeedState.DISCONNECTED, reason)
            self._gap_buffer.clear()

    def on_snapshot(
        self, snapshot_seq: int, snapshot_ts: float, book_data: Any | None = None
    ) -> bool:
        """
        Process an order book snapshot. Validates monotonicity against last processed timestamp.
        Returns True if accepted and transitioned to LIVE, False if rejected as stale.
        """
        with self._lock:
            # Stale snapshot validation: snapshot cannot be older than previously processed ticks
            if snapshot_ts < self._last_processed_ts and self._last_processed_ts > 0.0:
                self.metrics.stale_snapshots_rejected += 1
                self._set_state(
                    FeedState.STALE_SNAPSHOT,
                    f"Snapshot timestamp {snapshot_ts} < last processed {self._last_processed_ts}",
                )
                return False

            self.expected_seq = snapshot_seq + 1
            self._last_processed_ts = snapshot_ts
            self._gap_buffer.clear()
            self._set_state(
                FeedState.LIVE,
                f"Snapshot accepted at seq {snapshot_seq}, expected {self.expected_seq}",
            )
            return True

    def on_packet(self, packet: FeedPacket) -> list[FeedPacket]:
        """
        Ingest an incoming market data packet from the live multicast / socket stream.

        Returns:
            list[FeedPacket]: Packets ready for downstream dispatch in strictly monotonic order.
        """
        with self._lock:
            seq = packet.sequence_num

            # 1. Non-live states cannot dispatch packets directly
            if self._state not in (
                FeedState.LIVE,
                FeedState.RECOVERING,
                FeedState.GAP_DETECTED,
            ):
                logger.debug(
                    "[%s] Dropping packet seq %d while in non-live state %s",
                    self.channel_id,
                    seq,
                    self._state.value,
                )
                return []

            # 2. Duplicate Check: O(1) Fast Rejection
            if seq < self.expected_seq:
                self.metrics.dedup_dropped += 1
                return []

            # 3. Gap Detection (seq > expected_seq)
            if seq > self.expected_seq:
                self.metrics.gaps_detected += 1
                if len(self._gap_buffer) >= self.max_buffer_size:
                    self.on_recovery_failed(
                        f"Gap buffer saturated ({len(self._gap_buffer)} >= {self.max_buffer_size})"
                    )
                    return []

                self._gap_buffer[seq] = packet
                if self._state == FeedState.LIVE:
                    self._set_state(
                        FeedState.GAP_DETECTED,
                        f"Sequence jump detected: expected {self.expected_seq}, got {seq}",
                    )
                    self._trigger_recovery(self.expected_seq, seq - 1)
                return []

            # 4. In-Order Packet Arrival (seq == expected_seq)
            if self._state == FeedState.RECOVERING:
                # If currently recovering earlier missing packets, buffer this packet
                self._gap_buffer[seq] = packet
                return []

            # Direct dispatch in LIVE state
            dispatched = [packet]
            self.expected_seq += 1
            self._last_processed_ts = packet.timestamp

            # Drain contiguous packets from gap buffer if any were waiting
            if self.expected_seq in self._gap_buffer:
                dispatched.extend(self._drain_contiguous())

            self.metrics.in_order_dispatched += len(dispatched)
            return dispatched

    def _trigger_recovery(self, start_seq: int, end_seq: int) -> None:
        """Enter RECOVERING state and trigger replay handler."""
        self._set_state(
            FeedState.RECOVERING,
            f"Triggering replay for range [{start_seq}..{end_seq}]",
        )
        self.metrics.replays_requested += 1

        if self.replay_handler:
            try:
                replayed = self.replay_handler(self.channel_id, start_seq, end_seq)
                if replayed:
                    self.on_recovery_packets(replayed)
            except Exception as exc:
                logger.error("[%s] Replay handler failed: %s", self.channel_id, exc)
                self.on_recovery_failed(f"Replay handler error: {exc}")

    def on_recovery_packets(self, packets: list[FeedPacket]) -> list[FeedPacket]:
        """
        Ingest historical packets retrieved via TCP replay or backfill archive.
        Stitches missing gap packets and transitions back to LIVE once gap is healed.
        """
        with self._lock:
            if self._state != FeedState.RECOVERING:
                # Discard unexpected recovery packets
                self.metrics.dedup_dropped += len(packets)
                return []

            for p in packets:
                p.is_replayed = True
                if p.sequence_num >= self.expected_seq:
                    self._gap_buffer[p.sequence_num] = p
                else:
                    self.metrics.dedup_dropped += 1

            # Check if expected_seq is now satisfied
            if self.expected_seq in self._gap_buffer:
                dispatched = self._drain_contiguous()
                self.metrics.replays_completed += 1
                self.metrics.in_order_dispatched += len(dispatched)
                self._set_state(
                    FeedState.LIVE,
                    f"Gap recovery resolved, advanced expected_seq to {self.expected_seq}",
                )
                return dispatched

            return []

    def _drain_contiguous(self) -> list[FeedPacket]:
        """Drain contiguous sequence numbers from gap buffer starting at expected_seq."""
        dispatched: list[FeedPacket] = []
        while self.expected_seq in self._gap_buffer:
            pkt = self._gap_buffer.pop(self.expected_seq)
            dispatched.append(pkt)
            self._last_processed_ts = max(self._last_processed_ts, pkt.timestamp)
            self.expected_seq += 1
        return dispatched

    def on_recovery_failed(self, reason: str) -> None:
        """Mark recovery as unrecoverable to quarantine the feed and halt downstream trading."""
        with self._lock:
            self.metrics.recovery_failures += 1
            self._set_state(FeedState.FAILED_RECOVERY, reason)
            logger.critical(
                "[%s] Unrecoverable feed gap: %s. State locked in FAILED_RECOVERY.",
                self.channel_id,
                reason,
            )

    def stats(self) -> dict[str, Any]:
        """Return diagnostic metrics and current state."""
        with self._lock:
            return {
                "channel_id": self.channel_id,
                "state": self._state.value,
                "expected_seq": self.expected_seq,
                "buffered_packets": len(self._gap_buffer),
                "metrics": self.metrics.to_dict(),
            }


# Backwards compatibility alias
FeedRecoveryStateMachine = FeedRecoveryEngine
