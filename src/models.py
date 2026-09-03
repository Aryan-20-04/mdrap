"""
Canonical market event model and shared enums.

This is the single normalized representation every event is converted
into before quality checks, reconciliation, and storage. See
docs/data-model.md for field-by-field rationale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    TRADE = "TRADE"
    QUOTE = "QUOTE"


class QualityStatus(str, Enum):
    VALID = "VALID"
    SUSPICIOUS = "SUSPICIOUS"
    INVALID = "INVALID"


# Reason codes attached to SUSPICIOUS/INVALID events. Kept as short,
# stable strings so they can be aggregated in metrics and lineage.
class Reason(str, Enum):
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    DUPLICATE = "DUPLICATE"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    STALE = "STALE"
    PRICE_ANOMALY = "PRICE_ANOMALY"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    CROSS_FEED_DISAGREEMENT = "CROSS_FEED_DISAGREEMENT"
    MALFORMED = "MALFORMED"


@dataclass(slots=True)
class RawEvent:
    """What the feed simulator / gateway hands to the pipeline before
    normalization. Deliberately loose typing -- this is meant to model
    "whatever a vendor sent us", including malformed payloads."""
    source: str
    payload: dict
    receive_timestamp: float = 0.0
    raw_id: str = ""


@dataclass(slots=True)
class CanonicalEvent:
    event_id: str
    instrument_id: str
    event_type: EventType
    exchange_timestamp: float
    receive_timestamp: float
    processing_timestamp: float
    source: str
    sequence_number: Optional[int]
    price: Optional[float] = None
    quantity: Optional[float] = None
    bid_price: Optional[float] = None
    bid_size: Optional[float] = None
    ask_price: Optional[float] = None
    ask_size: Optional[float] = None
    quality_status: QualityStatus = QualityStatus.VALID
    reasons: list[str] = field(default_factory=list)
    raw_id: str = ""

    def dedup_key(self) -> tuple:
        if self.sequence_number is not None:
            return (self.source, self.instrument_id, self.sequence_number)
        # Include quote fields so distinct quotes at the same timestamp don't collide.
        if self.event_type == EventType.QUOTE:
            return (
                self.source, self.instrument_id, self.event_type.value,
                round(self.exchange_timestamp, 6),
                self.bid_price, self.ask_price, self.bid_size, self.ask_size,
            )
        return (
            self.source, self.instrument_id, self.event_type.value,
            round(self.exchange_timestamp, 6), self.price, self.quantity,
        )
