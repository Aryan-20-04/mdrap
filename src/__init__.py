"""Market Data Reliability & Acceleration Platform (MDRAP).

Institutional market-data infrastructure for converting noisy, delayed,
duplicated, inconsistent market data from multiple sources into a fast,
validated, canonical real-time data stream.
"""

from __future__ import annotations

from client import Client, MDRAPClient, MarketEvent
from models import CanonicalEvent, EventType, QualityStatus, Reason, deprecated
from _version import __version__
from mdrap import Engine

__stability__ = "stable"

__all__ = [
    "__stability__",
    "__version__",
    "Client",
    "MDRAPClient",
    "MarketEvent",
    "CanonicalEvent",
    "EventType",
    "QualityStatus",
    "Reason",
    "deprecated",
    "Engine",
]
