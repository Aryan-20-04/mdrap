"""Canonical market event model and shared domain enumerations.

This module defines the single normalized representation that every incoming market
data feed is converted into before undergoing data-quality checks, cross-feed
reconciliation, and persistent storage.

Architectural Principles (from MDRAP System Specification §26):
  1. Correctness before optimization: Schema normalization guarantees strict types.
  2. Never silently discard bad data: Events with missing or invalid fields are
     retained, tagged with Reason codes, and routed to quarantine.
  3. Memory layout efficiency: Dataclasses utilize `__slots__ = True` to eliminate
     per-instance `__dict__` overhead, drastically reducing memory footprint and
     improving cache locality in high-throughput hot paths.
  4. Lineage tracking: Every CanonicalEvent retains its originating `raw_id` and
     `source` identifier, ensuring complete provenance back to raw vendor ticks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__stability__ = "stable"


class EventType(str, Enum):
    """Enumeration of market data event categories."""

    TRADE = "TRADE"
    QUOTE = "QUOTE"
    UNKNOWN = "UNKNOWN"


class QualityStatus(str, Enum):
    """Data quality classification status.

    Status Priority Hierarchy:
        INVALID (2) > SUSPICIOUS (1) > VALID (0)

    In accordance with MDRAP design invariants, an event's quality status can
    never be downgraded (e.g. an INVALID event can never transition back to
    SUSPICIOUS or VALID).
    """

    VALID = "VALID"
    SUSPICIOUS = "SUSPICIOUS"
    INVALID = "INVALID"


# Reason codes attached to SUSPICIOUS or INVALID events. Kept as short,
# stable uppercase strings so they can be efficiently indexed, queried,
# and aggregated in metrics and lineage records.
class Reason(str, Enum):
    """Deterministic failure and anomaly reason codes."""

    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"  # Bit 0: Schema validation failure or non-finite values
    DUPLICATE = "DUPLICATE"  # Bit 1: Duplicate sequence or event ID already observed
    SEQUENCE_GAP = "SEQUENCE_GAP"  # Bit 2: Missing sequence numbers indicating packet drop
    OUT_OF_ORDER = "OUT_OF_ORDER"  # Bit 3: Decreasing or non-monotonic sequence or timestamp
    STALE = "STALE"  # Bit 4: Timestamp exceeds maximum allowed staleness threshold
    PRICE_ANOMALY = "PRICE_ANOMALY"  # Bit 5: Price movement exceeds statistical volatility threshold
    CROSSED_QUOTE = "CROSSED_QUOTE"  # Bit 6: Bid price greater than or equal to ask price
    CROSS_FEED_DISAGREEMENT = "CROSS_FEED_DISAGREEMENT"  # Bit 7: Consensus divergence between redundant feeds
    MALFORMED = "MALFORMED"  # Bit 8: Malformed message payload or invalid field format
    CIRCUIT_FILTER_BREACH = "CIRCUIT_FILTER_BREACH"  # Bit 9: NSE/BSE daily price band limit breach (+/- 10%)
    VOLATILITY_INTERRUPTION = "VOLATILITY_INTERRUPTION"  # Bit 10: Deutsche Boerse dynamic price corridor halt (+/- 5%)
    SPECIAL_QUOTE_INDICATION = "SPECIAL_QUOTE_INDICATION"  # Bit 11: Tokyo Stock Exchange Tokuhai quote indication (+/- 8%)
    TS_IMPLAUSIBLE = "TS_IMPLAUSIBLE"  # Bit 12: exchange timestamp implausibly ahead of receive time
    RATE_LIMITED = "RATE_LIMITED"  # Bit 13: Ingress rate limit exceeded for source
    SECURITY_REJECT = "SECURITY_REJECT"  # Bit 14: Security gate rejection (sanitizer, HMAC, or auth failure)

class AssetClass(str, Enum):
    """Supported asset classes across global venues."""

    EQUITY = "EQUITY"
    CRYPTO = "CRYPTO"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    BOND = "BOND"
    FX = "FX"


@dataclass(slots=True)
class FuturesContract:
    """Specification metadata for exchange-traded futures derivatives."""

    symbol: str
    underlying: str
    expiry_date: str
    contract_size: float = 1.0
    tick_size: float = 0.01
    settlement_type: str = "PHYSICAL"


@dataclass(slots=True)
class OptionDerivative:
    """Specification metadata for listed equity/index options."""

    symbol: str
    underlying: str
    strike: float
    expiry_date: str
    option_type: str = "CALL"
    contract_multiplier: float = 100.0


@dataclass(slots=True)
class BondSecurity:
    """Specification metadata for fixed income securities and corporate bonds."""

    cusip: str
    issuer: str
    coupon_rate: float
    maturity_date: str
    face_value: float = 1000.0
    payment_frequency: int = 2


@dataclass(slots=True)
class RawEvent:
    """Raw ingestion payload handed to the pipeline before normalization.

    Deliberately loose typing: this captures 'whatever the vendor feed delivered',
    including corrupted bytes, missing keys, or type-coerced JSON strings.
    """

    source: str
    payload: dict
    receive_timestamp: float = 0.0
    raw_id: str = ""
    wire: str | bytes = ""


@dataclass(slots=True)
class CanonicalEvent:
    """Standardized, normalized, validated market data record.

    Memory Layout:
        `slots=True` eliminates the instance dictionary overhead, allocating
        fixed-size descriptor offsets for each field. This is vital when buffering
        millions of events per second in memory.

    Timestamp Conventions:
        - exchange_timestamp: Source venue clock time (seconds since Unix epoch).
        - receive_timestamp: Gateway wall-clock ingress time (seconds since Unix epoch).
        - processing_timestamp: Quality engine completion time (seconds since Unix epoch).
    """

    event_id: str
    instrument_id: str
    event_type: EventType
    exchange_timestamp: float
    receive_timestamp: float
    processing_timestamp: float
    source: str
    sequence_number: int | None
    price: float | None = None
    quantity: float | None = None
    bid_price: float | None = None
    bid_size: float | None = None
    ask_price: float | None = None
    ask_size: float | None = None
    quality_status: QualityStatus = QualityStatus.VALID
    reasons: list[str] = field(default_factory=list)
    raw_id: str = ""
    source_id: int = -1
    instrument_id_int: int = -1

    # Multi-asset class extensions
    asset_class: AssetClass = AssetClass.EQUITY
    expiry_date: str | None = None
    contract_size: float | None = None
    underlying_id: str | None = None
    open_interest: float | None = None
    strike: float | None = None
    put_call: str | None = None
    implied_vol: float | None = None
    delta: float | None = None
    gamma: float | None = None
    coupon: float | None = None
    maturity_date: str | None = None
    yield_to_worst: float | None = None
    duration: float | None = None

    # Global exchange metadata
    venue: str = "XNAS"
    currency: str = "USD"
    clock_source: str = "HOST_SYS_CLOCK"  # MiFID II RTS 25 timestamp traceability

    def dedup_key(self) -> tuple:
        """Construct a deterministic hashable key for duplicate detection.

        If the upstream feed provides monotonic integer sequence numbers, the
        tuple `(source, instrument_id, sequence_number)` is uniquely identifying.

        If sequence numbers are absent (e.g. REST snapshots or unsequenced websockets),
        a content-based fingerprint tuple is computed. Microsecond rounding
        (`round(self.exchange_timestamp, 6)`) prevents IEEE-754 floating-point
        precision jitter from triggering false negative duplicate evaluations.
        """
        if self.sequence_number is not None:
            return (self.source, self.instrument_id, self.sequence_number)

        # Include quote fields so distinct quotes at the same timestamp don't collide.
        if self.event_type == EventType.QUOTE:
            return (
                self.source,
                self.instrument_id,
                self.event_type.value,
                round(self.exchange_timestamp, 6),
                self.bid_price,
                self.ask_price,
                self.bid_size,
                self.ask_size,
            )
        return (
            self.source,
            self.instrument_id,
            self.event_type.value,
            round(self.exchange_timestamp, 6),
            self.price,
            self.quantity,
        )
