"""MDRAP Clock Abstraction.

Provides a unified time protocol allowing deterministic time injection
in trading engine pipelines, simulation, replay, and unit tests without
monkeypatching system time.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

__stability__ = "stable"



@runtime_checkable
class Clock(Protocol):
    """Protocol representing a time source."""

    def now(self) -> float:
        """Return the current epoch timestamp in seconds as a float."""
        ...


class SystemClock:
    """Standard wall-clock time source backed by time.time()."""

    def now(self) -> float:
        return time.time()

    def __repr__(self) -> str:
        return "SystemClock()"


class FixedClock:
    """Deterministic, controllable clock for testing, replay, and doc examples."""

    def __init__(self, current_time: float = 0.0) -> None:
        self._current_time: float = float(current_time)

    def now(self) -> float:
        return self._current_time

    def set(self, timestamp: float) -> None:
        self._current_time = float(timestamp)

    def advance(self, delta_s: float) -> None:
        self._current_time += float(delta_s)

    def __repr__(self) -> str:
        return f"FixedClock(current_time={self._current_time:.6f})"
