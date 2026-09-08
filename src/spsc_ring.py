"""
Lock-Free Single-Producer Single-Consumer (SPSC) Circular Ring Buffer.

Implements the core messaging queue primitive of the LMAX Disruptor pattern
for ultra-low latency, zero-lock inter-thread handoffs across pipeline stages.

Key Characteristics:
- Power-of-two capacity with fast bitwise masking: index = seq & mask
- Lock-free for single producer and single consumer threads:
  Producer only mutates write index (tail); Consumer only mutates read index (head).
- Batch drainage support (`drain_into`) for high-throughput vectorized consumer stages.
- Graceful overflow management with non-blocking `offer()` and optional drop tracking.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Generic, List, Optional, TypeVar

T = TypeVar("T")


def _next_power_of_two(n: int) -> int:
    """Return smallest power of two >= n."""
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


class SPSCRingBuffer(Generic[T]):
    """
    Lock-free Single-Producer Single-Consumer (SPSC) bounded circular ring buffer.
    Thread-safe without locks between exactly one producer thread and one consumer thread.
    """

    __slots__ = (
        "capacity",
        "mask",
        "_buffer",
        "_head",
        "_tail",
        "_total_pushed",
        "_total_popped",
        "_total_dropped",
    )

    def __init__(self, capacity: int = 16384):
        self.capacity = _next_power_of_two(max(16, capacity))
        self.mask = self.capacity - 1
        # Pre-allocate array slots
        self._buffer: List[Optional[T]] = [None] * self.capacity
        # Head = read index (only modified by consumer)
        self._head = 0
        # Tail = write index (only modified by producer)
        self._tail = 0
        # Telemetry counters
        self._total_pushed = 0
        self._total_popped = 0
        self._total_dropped = 0

    @property
    def total_pushed(self) -> int:
        return self._total_pushed

    @property
    def total_popped(self) -> int:
        return self._total_popped

    @property
    def total_dropped(self) -> int:
        return self._total_dropped

    def size(self) -> int:
        """Current number of items waiting in the buffer (approximate due to concurrency)."""
        diff = self._tail - self._head
        return max(0, min(self.capacity, diff))

    def is_empty(self) -> bool:
        return self._tail == self._head

    def is_full(self) -> bool:
        return (self._tail - self._head) >= self.capacity

    def remaining_capacity(self) -> int:
        return max(0, self.capacity - (self._tail - self._head))

    def offer(self, item: T, track_drop: bool = False) -> bool:
        """
        Non-blocking append by the producer thread.
        Returns True if item was enqueued, False if buffer is full.
        """
        tail = self._tail
        head = self._head
        if (tail - head) >= self.capacity:
            if track_drop:
                self._total_dropped += 1
            return False

        # Place item into slot before exposing tail
        self._buffer[tail & self.mask] = item
        self._tail = tail + 1
        self._total_pushed += 1
        return True

    def offer_or_drop(self, item: T) -> bool:
        """Offer item, recording drop telemetry if buffer is full."""
        return self.offer(item, track_drop=True)

    def poll(self) -> Optional[T]:
        """
        Non-blocking dequeue by the consumer thread.
        Returns item if available, or None if buffer is empty.
        """
        head = self._head
        tail = self._tail
        if head >= tail:
            return None

        idx = head & self.mask
        item = self._buffer[idx]
        self._buffer[idx] = None  # Allow GC of payload
        self._head = head + 1
        self._total_popped += 1
        return item

    def drain_into(self, target_list: List[T], max_items: int = 2000) -> int:
        """
        Batch drainage by the consumer thread.
        Drains up to max_items into target_list in a fast loop.
        Returns the number of items appended.
        """
        head = self._head
        tail = self._tail
        available = tail - head
        if available <= 0:
            return 0

        to_drain = min(available, max_items)
        mask = self.mask
        buf = self._buffer

        for i in range(to_drain):
            idx = (head + i) & mask
            target_list.append(buf[idx])  # type: ignore
            buf[idx] = None

        self._head = head + to_drain
        self._total_popped += to_drain
        return to_drain

    def clear(self) -> None:
        """Clear all contents and reset indices (call only when quiescent)."""
        self._head = self._tail
        self._buffer = [None] * self.capacity

    def stats(self) -> dict:
        """Return diagnostic health telemetry."""
        cur_size = self.size()
        return {
            "capacity": self.capacity,
            "size": cur_size,
            "utilization_pct": round((cur_size / self.capacity) * 100.0, 2),
            "total_pushed": self._total_pushed,
            "total_popped": self._total_popped,
            "total_dropped": self._total_dropped,
        }
