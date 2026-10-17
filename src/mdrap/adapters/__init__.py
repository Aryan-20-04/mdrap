"""MDRAP Feed Adapter Protocol, Standard Adapters & Dynamic Discovery (Phase 4).

Provides:
- Institutional FeedAdapter Protocol.
- Standardized Adapters: BinanceAdapter, KrakenAdapter, DatabentoAdapter, ITCHAdapter.
- Dynamic discovery via standard library entry_points.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import CanonicalEvent, RawEvent
from .base import (
    AdapterCapabilities,
    AdapterStreamState,
    BaseFeedAdapter,
    LicensingTier,
    ReconnectTracker,
    SequenceTracker,
)
from .binance import BinanceAdapter
from .databento import DatabentoAdapter
from .itch import ITCHAdapter
from .kraken import KrakenAdapter
from .reference import ReferenceFeedAdapter
from .template import TemplateCustomVenueAdapter

__all__ = [
    "FeedAdapter",
    "discover_adapters",
    "BaseFeedAdapter",
    "AdapterCapabilities",
    "AdapterStreamState",
    "LicensingTier",
    "SequenceTracker",
    "ReconnectTracker",
    "BinanceAdapter",
    "KrakenAdapter",
    "DatabentoAdapter",
    "ITCHAdapter",
    "ReferenceFeedAdapter",
    "TemplateCustomVenueAdapter",
]

__stability__ = "stable"


@runtime_checkable
class FeedAdapter(Protocol):
    """Institutional protocol for external exchange and market data feed adapters."""

    def connect(self) -> None:
        """Initialize connection, establish TCP/UDP sockets, or open file streams."""
        ...

    def disconnect(self) -> None:
        """Gracefully close sessions and release transport resources."""
        ...

    def receive(self) -> RawEvent | None:
        """Receive the next raw event from the upstream feed buffer."""
        ...

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate upstream venue framing into canonical MDRAP event schema."""
        ...

    def health(self) -> dict:
        """Return connectivity state, packet rates, and stream diagnostics."""
        ...

    # Backward compatibility aliases
    def open(self) -> None:
        """Legacy alias for connect()."""
        ...

    def close(self) -> None:
        """Legacy alias for disconnect()."""
        ...

    @classmethod
    def __subclasshook__(cls, other: type) -> bool:
        if cls is not FeedAdapter:
            return NotImplemented
        has_modern = all(
            any(m in b.__dict__ for b in other.__mro__) for m in ("connect", "receive")
        )
        has_stream = all(
            any(m in b.__dict__ for b in other.__mro__) for m in ("open", "close")
        )
        if has_modern or has_stream:
            return True
        return NotImplemented


def discover_adapters() -> dict[str, type[FeedAdapter]]:
    """Discover third-party feed adapters registered under the entrypoint 'mdrap.adapters'."""
    from ..plugins import registry

    discovered = registry.discover("mdrap.adapters", validate=True)
    adapters: dict[str, type[FeedAdapter]] = {}
    for name, obj in discovered.items():
        if isinstance(obj, type) and issubclass(obj, FeedAdapter):
            adapters[name] = obj
        elif not isinstance(obj, type) and isinstance(obj, FeedAdapter):
            adapters[name] = obj.__class__  # type: ignore[assignment]
    return adapters
