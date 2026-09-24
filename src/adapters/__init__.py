"""MDRAP Feed Adapter Protocol & Dynamic Discovery.

Provides the FeedAdapter Protocol for exchange feed ingestion and dynamic
discovery via standard library `importlib.metadata.entry_points(group="mdrap.adapters")`.
"""
from __future__ import annotations

import sys
from typing import Iterator, Protocol, runtime_checkable

from models import RawEvent

__all__ = ["FeedAdapter", "discover_adapters"]

__stability__ = "stable"


@runtime_checkable
class FeedAdapter(Protocol):
    """Protocol for external exchange feed ingest adapters."""

    def open(self) -> None:
        """Initialize connection, file handles, or network sockets."""
        ...

    def __iter__(self) -> Iterator[RawEvent]:
        """Stream raw incoming market events."""
        ...

    def close(self) -> None:
        """Gracefully release all resources."""
        ...


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
                if isinstance(adapter_cls, type) and issubclass(adapter_cls, FeedAdapter):
                    adapters[ep.name] = adapter_cls
            except Exception:
                pass
    except Exception:
        pass
    return adapters
