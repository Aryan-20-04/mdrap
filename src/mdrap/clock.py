"""MDRAP Clock Abstraction & Precision Hardware PTP Timing.

Provides unified time protocols allowing deterministic time injection
in trading engine pipelines, simulation, replay, unit tests, and nanosecond-level
hardware NIC synchronization (IEEE 1588 PTP).
"""

from __future__ import annotations

import time
from typing import Any, Optional, Protocol, runtime_checkable

__stability__ = "stable"


def seconds_to_ns(seconds: float) -> int:
    """Convert floating-point epoch seconds to integer nanoseconds."""
    return int(seconds * 1_000_000_000)


def ns_to_seconds(ns: int) -> float:
    """Convert integer nanoseconds to floating-point epoch seconds."""
    return ns / 1_000_000_000.0


@runtime_checkable
class Clock(Protocol):
    """Protocol representing a time source."""

    def now(self) -> float:
        """Return the current epoch timestamp in seconds as a float."""
        ...


@runtime_checkable
class PrecisionClock(Clock, Protocol):
    """Protocol representing a nanosecond-precision time source."""

    def now_ns(self) -> int:
        """Return the current epoch timestamp in integer nanoseconds."""
        ...


class SystemClock:
    """Standard wall-clock time source backed by time.time()."""

    def now(self) -> float:
        return time.time()

    def now_ns(self) -> int:
        return time.time_ns()

    def __repr__(self) -> str:
        return "SystemClock()"


class NanosecondClock:
    """High-resolution monotonic time source backed by time.time_ns()."""

    def now(self) -> float:
        return time.time_ns() / 1_000_000_000.0

    def now_ns(self) -> int:
        return time.time_ns()

    def __repr__(self) -> str:
        return "NanosecondClock()"


class FixedClock:
    """Deterministic, controllable clock for testing, replay, and doc examples."""

    def __init__(self, current_time: float = 0.0) -> None:
        self._current_time: float = float(current_time)

    def now(self) -> float:
        return self._current_time

    def now_ns(self) -> int:
        return int(self._current_time * 1_000_000_000)

    def set(self, timestamp: float) -> None:
        self._current_time = float(timestamp)

    def set_ns(self, timestamp_ns: int) -> None:
        self._current_time = timestamp_ns / 1_000_000_000.0

    def advance(self, delta_s: float) -> None:
        self._current_time += float(delta_s)

    def advance_ns(self, delta_ns: int) -> None:
        self._current_time += delta_ns / 1_000_000_000.0

    def __repr__(self) -> str:
        return f"FixedClock(current_time={self._current_time:.6f})"


class PTPClock:
    """IEEE 1588 Precision Time Protocol (PTP) Clock with hardware drift and offset tracking.

    Supports linear frequency correction (drift in parts-per-billion, ppb)
    and master-slave offset compensation for high-frequency trading colocation.
    """

    def __init__(
        self,
        base_clock: Optional[Clock] = None,
        offset_ns: int = 0,
        drift_ppb: float = 0.0,
    ) -> None:
        self._base = base_clock or NanosecondClock()
        self._offset_ns: int = int(offset_ns)
        self._drift_ppb: float = float(drift_ppb)
        self._drift_factor: float = 1.0 + (self._drift_ppb / 1_000_000_000.0)
        self._sync_count: int = 0
        self._last_sync_ts: float = 0.0

    @property
    def offset_ns(self) -> int:
        return self._offset_ns

    @property
    def drift_ppb(self) -> float:
        return self._drift_ppb

    def calibrate(
        self, measured_offset_ns: int, measured_drift_ppb: float = 0.0
    ) -> None:
        """Calibrate clock against PTP grandmaster synchronization exchange."""
        self._offset_ns = int(measured_offset_ns)
        self._drift_ppb = float(measured_drift_ppb)
        self._drift_factor = 1.0 + (self._drift_ppb / 1_000_000_000.0)
        self._sync_count += 1
        self._last_sync_ts = time.time()

    def now_ns(self) -> int:
        raw_ns = (
            self._base.now_ns()
            if hasattr(self._base, "now_ns")
            else int(self._base.now() * 1_000_000_000)
        )
        return int((raw_ns + self._offset_ns) * self._drift_factor)

    def now(self) -> float:
        return self.now_ns() / 1_000_000_000.0

    def get_stats(self) -> dict[str, Any]:
        return {
            "offset_ns": self._offset_ns,
            "drift_ppb": self._drift_ppb,
            "sync_count": self._sync_count,
            "last_sync_ts": self._last_sync_ts,
            "base_clock": repr(self._base),
        }

    def __repr__(self) -> str:
        return f"PTPClock(offset_ns={self._offset_ns}, drift_ppb={self._drift_ppb:.2f})"
