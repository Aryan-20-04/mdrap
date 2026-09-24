"""MDRAP Feed Adapter Protocol & Dynamic Discovery.

Provides the FeedAdapter Protocol for exchange feed ingestion and dynamic
discovery via standard library `importlib.metadata.entry_points(group="mdrap.adapters")`.
"""

from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from models import CanonicalEvent, RawEvent

__all__ = ["FeedAdapter", "discover_adapters"]

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
    adapters: dict[str, type[FeedAdapter]] = {}
    try:
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points

            eps = entry_points(group="mdrap.adapters")
        else:
            import importlib_metadata  # type: ignore

            eps = importlib_metadata.entry_points().get("mdrap.adapters", [])

        for ep in eps:
            try:
                adapter_cls = ep.load()
                if isinstance(adapter_cls, type) and issubclass(
                    adapter_cls, FeedAdapter
                ):
                    adapters[ep.name] = adapter_cls
            except Exception:
                pass
    except Exception:
        pass
    return adapters
