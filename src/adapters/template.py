"""Minimal Custom Venue FeedAdapter Template (< 50 lines)."""

from __future__ import annotations

import time
from typing import Iterator

from models import RawEvent

__stability__ = "stable"


class TemplateCustomVenueAdapter:
    """Minimal example of a custom exchange feed adapter implementing FeedAdapter."""

    def __init__(self, venue_name: str = "MY_CUSTOM_VENUE"):
        self.venue_name = venue_name
        self._is_open = False

    def open(self) -> None:
        self._is_open = True

    def __iter__(self) -> Iterator[RawEvent]:
        if not self._is_open:
            raise RuntimeError("Adapter is not opened. Call open() first.")
        # Stream 100 sample events
        for seq in range(1, 101):
            now = time.time()
            yield RawEvent(
                source=self.venue_name,
                payload={
                    "seq": seq,
                    "symbol": "CUSTOM_TICK",
                    "price": 100.0,
                    "qty": 1.0,
                    "exchange_ts": now - 0.001,
                },
                receive_timestamp=now,
            )

    def close(self) -> None:
        self._is_open = False
