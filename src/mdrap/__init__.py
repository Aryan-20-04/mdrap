"""Public MDRAP package API.

The package exports core event types and orchestration. Modules outside this
list are implementation details unless documented otherwise.
"""

from __future__ import annotations

from typing import Any

import os
import warnings

from ._version import __version__
from .client import Client, MDRAPClient, MarketEvent

from .models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
    ReasonRegistry,
    reason_registry,
    deprecated,
)
from .clock import Clock, FixedClock, SystemClock
from .engine import Engine, EngineConfig, EngineDecision, EngineState
from .ingestlog import IngestLog
from .pipeline import Pipeline
from .projection import SQLiteProjection
from .storage import Store

# Backward-compatible alias for deterministic engine
CoreEngine = Engine

# Check legacy environment variables on package initialization
if "MDRAP_ASYNC_WRITER" in os.environ:
    warnings.warn(
        "Environment variable 'MDRAP_ASYNC_WRITER' is deprecated in MDRAP v3.0.0. "
        "Configure durability and write policy explicitly via IngestLog / Engine / SQLiteProjection configuration.",
        DeprecationWarning,
        stacklevel=2,
    )
if "MDRAP_DISABLE_JOURNAL" in os.environ:
    warnings.warn(
        "Environment variable 'MDRAP_DISABLE_JOURNAL' is deprecated in MDRAP v3.0.0. "
        "IngestLog WAL is the mandatory durability boundary; use explicit configuration.",
        DeprecationWarning,
        stacklevel=2,
    )

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
    "ReasonRegistry",
    "reason_registry",
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
    "EngineConfig",
]

__stability__ = "stable"
