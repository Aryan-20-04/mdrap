"""Sequence Gap Lifecycle and Retransmission Management for MDRAP (Program I-6).

Tracks open sequence gaps through formal state machine:
    OPEN -> FILLED | EXPIRED | UNRECOVERABLE

Provides extension hooks to trigger upstream exchange retransmission / backfill.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Callable

__stability__ = "stable"


class GapState(str, Enum):
    """Lifecycle states of a sequence gap."""

    OPEN = "OPEN"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    UNRECOVERABLE = "UNRECOVERABLE"


@dataclass(slots=True)
class GapRecord:
    """Provenance and state record for a single missing sequence number."""

    gap_id: str
    source: str
    instrument: str
    missing_seq: int
    received_seq: int
    epoch: str
    opened_ts: float
    state: GapState = GapState.OPEN
    filled_ts: float | None = None
    expired_ts: float | None = None


OnGapHook = Callable[[str, str, int, int, str], None]


class GapManager:
    """Manages sequence gap lifecycle, filling, expiration, and user notification hooks."""

    def __init__(
        self,
        gap_timeout_s: float = 2.0,
        max_open_gaps: int = 10_000,
        on_gap_hook: OnGapHook | None = None,
    ) -> None:
        self.gap_timeout_s = gap_timeout_s
        self.max_open_gaps = max_open_gaps
        self.on_gap_hook = on_gap_hook
        self._open_gaps: dict[tuple[str, str, str, int], GapRecord] = {}
        self._open_order: deque[tuple[str, str, str, int]] = deque()
        self._filled_count: int = 0
        self._expired_count: int = 0
        self._unrecoverable_count: int = 0

    def record_gap(
        self,
        source: str,
        instrument: str,
        expected_start: int,
        received_seq: int,
        epoch: str = "0",
        now_ts: float | None = None,
    ) -> list[GapRecord]:
        """Record open sequence gaps between expected_start and received_seq - 1."""
        ts = now_ts if now_ts is not None else time.time()
        created: list[GapRecord] = []

        # Bound range to prevent unbounded memory allocation on corrupt huge jumps
        max_to_track = min(received_seq - expected_start, 1000)
        for s in range(expected_start, expected_start + max_to_track):
            coord = (source, instrument, epoch, s)
            if coord not in self._open_gaps:
                gap_id = f"gap_{source}_{instrument}_{epoch}_{s}"
                rec = GapRecord(
                    gap_id=gap_id,
                    source=source,
                    instrument=instrument,
                    missing_seq=s,
                    received_seq=received_seq,
                    epoch=epoch,
                    opened_ts=ts,
                    state=GapState.OPEN,
                )
                self._open_gaps[coord] = rec
                self._open_order.append(coord)
                created.append(rec)

                # Enforce bounded capacity
                if len(self._open_gaps) > self.max_open_gaps:
                    old_coord = self._open_order.popleft()
                    old_rec = self._open_gaps.pop(old_coord, None)
                    if old_rec and old_rec.state == GapState.OPEN:
                        old_rec.state = GapState.UNRECOVERABLE
                        self._unrecoverable_count += 1

        if self.on_gap_hook is not None and created:
            try:
                self.on_gap_hook(
                    source, instrument, expected_start, received_seq, epoch
                )
            except Exception:
                pass

        return created

    def try_fill(
        self,
        source: str,
        instrument: str,
        seq: int,
        epoch: str = "0",
        now_ts: float | None = None,
    ) -> GapRecord | None:
        """Attempt to fill an open sequence gap with an arriving packet."""
        coord = (source, instrument, epoch, seq)
        rec = self._open_gaps.pop(coord, None)
        if rec is not None:
            ts = now_ts if now_ts is not None else time.time()
            rec.state = GapState.FILLED
            rec.filled_ts = ts
            self._filled_count += 1
            return rec
        return None

    def drain_expired(self, now_ts: float | None = None) -> list[GapRecord]:
        """Evict gaps that have exceeded gap_timeout_s and transition them to EXPIRED."""
        ts = now_ts if now_ts is not None else time.time()
        expired: list[GapRecord] = []
        expired_coords: list[tuple[str, str, str, int]] = []

        for coord, rec in self._open_gaps.items():
            if ts - rec.opened_ts >= self.gap_timeout_s:
                rec.state = GapState.EXPIRED
                rec.expired_ts = ts
                expired.append(rec)
                expired_coords.append(coord)
                self._expired_count += 1

        for coord in expired_coords:
            self._open_gaps.pop(coord, None)

        return expired

    def get_open_gaps(self) -> list[GapRecord]:
        """Return list of all currently open GapRecords."""
        return list(self._open_gaps.values())

    def stats(self) -> dict[str, int]:
        """Return operational counters for gap lifecycle."""
        return {
            "open": len(self._open_gaps),
            "filled": self._filled_count,
            "expired": self._expired_count,
            "unrecoverable": self._unrecoverable_count,
        }
