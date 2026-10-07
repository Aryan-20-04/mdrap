"""Public MDRAP package API.

The package exports core event types and orchestration. Modules outside this
list are implementation details unless documented otherwise.
"""

from __future__ import annotations

from typing import Any

from ._version import __version__
from .client import Client, MDRAPClient, MarketEvent

from .models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
    deprecated,
)
from .clock import Clock, FixedClock, SystemClock
from .engine import Engine as CoreEngine, EngineDecision, EngineState
from .ingestlog import IngestLog
from .pipeline import Pipeline
from .projection import SQLiteProjection
from .storage import Store

__all__ = [
    "__version__",
    "Client",
    "MDRAPClient",
    "MarketEvent",
    "RawEvent",
    "CanonicalEvent",
    "EventType",
    "QualityStatus",
    "Reason",
    "deprecated",
    "Clock",
    "FixedClock",
    "SystemClock",
    "IngestLog",
    "CoreEngine",
    "EngineDecision",
    "EngineState",
    "SQLiteProjection",
    "Pipeline",
    "Store",
    "Engine",
]

__stability__ = "stable"


class Engine:
    """Unified high-level facade for MDRAP ingestion, validation, and storage (Phase 7).

    Provides an intuitive single entrypoint to initialize and run the MDRAP pipeline:
        import mdrap

        with mdrap.Engine(db_path=":memory:") as engine:
            canon = engine.process(raw_event)
            recent = engine.query("AAPL")
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        durability: str = "balanced",
        quality: Any = None,
        async_writer: bool | None = None,
        journal: bool | str | None = None,
        **kwargs: Any,
    ):
        self.store = Store(path=db_path, durability=durability)
        self.pipeline = Pipeline(
            store=self.store,
            quality=quality,
            async_writer=async_writer,
            journal=journal,
            **kwargs,
        )

    def process(self, raw: RawEvent) -> CanonicalEvent | None:
        """Process a single raw event through the validation pipeline."""
        return self.pipeline.process_one(raw)

    def process_one(self, raw: RawEvent) -> CanonicalEvent | None:
        """Alias for process()."""
        return self.pipeline.process_one(raw)

    def process_batch(self, raw_events: list[RawEvent]) -> list[CanonicalEvent]:
        """Process a batch of raw events through the pipeline."""
        return self.pipeline.process_batch(raw_events)

    def flush(self, wait: bool = True) -> None:
        """Flush pending batches to persistent storage."""
        self.pipeline.flush(wait=wait)

    def query(self, instrument: str, limit: int = 100) -> list[dict]:
        """Query recent canonical ticks for an instrument."""
        return self.store.latest(instrument, limit=limit)

    def metrics(self) -> dict:
        """Return pipeline performance metrics and health summary."""
        return self.pipeline.metrics.summary()

    def close(self) -> None:
        """Finish processing and release storage and journal resources."""
        self.pipeline.finish()
        self.store.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
