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


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    CRYPTO = "CRYPTO"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    BOND = "BOND"
    FX = "FX"


@dataclass(slots=True)
class FuturesContract:
    symbol: str
    underlying: str
    expiry_date: str
    contract_size: float = 1.0
    tick_size: float = 0.01
    settlement_type: str = "PHYSICAL"


@dataclass(slots=True)
class OptionDerivative:
    symbol: str
    underlying: str
    strike: float
    expiry_date: str
    option_type: str = "CALL"
    contract_multiplier: float = 100.0


@dataclass(slots=True)
class BondSecurity:
    cusip: str
    issuer: str
    coupon_rate: float
    maturity_date: str
    face_value: float = 1000.0
    payment_frequency: int = 2


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
    # Multi-asset class extensions
    asset_class: AssetClass = AssetClass.EQUITY
    expiry_date: Optional[str] = None
    contract_size: Optional[float] = None
    underlying_id: Optional[str] = None
    open_interest: Optional[float] = None
    strike: Optional[float] = None
    put_call: Optional[str] = None
    implied_vol: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    coupon: Optional[float] = None
    maturity_date: Optional[str] = None
    yield_to_worst: Optional[float] = None
    duration: Optional[float] = None

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
