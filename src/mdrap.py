"""Market Data Reliability & Acceleration Platform (MDRAP).

Institutional market-data infrastructure for converting noisy, delayed,
duplicated, inconsistent market data from multiple sources into a fast,
validated, canonical real-time data stream.
"""

from __future__ import annotations

from client import Client, MDRAPClient, MarketEvent
from models import CanonicalEvent, EventType, QualityStatus, Reason

__version__ = "2.3.0"

__all__ = [
    "__version__",
    "Client",
    "MDRAPClient",
    "MarketEvent",
    "CanonicalEvent",
    "EventType",
    "QualityStatus",
    "Reason",
]

__stability__ = "stable"
