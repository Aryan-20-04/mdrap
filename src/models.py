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
import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

__stability__ = "stable"


class EventType(str, Enum):
    """Enumeration of market data event categories."""

    TRADE = "TRADE"
    QUOTE = "QUOTE"
    BBO = "BBO"
    BOOK = "BOOK"
    DEPTH = "DEPTH"
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

    SCHEMA_VIOLATION = (
        "SCHEMA_VIOLATION"  # Bit 0: Schema validation failure or non-finite values
    )
    DUPLICATE = "DUPLICATE"  # Bit 1: Duplicate sequence or event ID already observed
    SEQUENCE_GAP = (
        "SEQUENCE_GAP"  # Bit 2: Missing sequence numbers indicating packet drop
    )
    OUT_OF_ORDER = (
        "OUT_OF_ORDER"  # Bit 3: Decreasing or non-monotonic sequence or timestamp
    )
    STALE = "STALE"  # Bit 4: Timestamp exceeds maximum allowed staleness threshold
    PRICE_ANOMALY = "PRICE_ANOMALY"  # Bit 5: Price movement exceeds statistical volatility threshold
    CROSSED_QUOTE = (
        "CROSSED_QUOTE"  # Bit 6: Bid price greater than or equal to ask price
    )
    CROSS_FEED_DISAGREEMENT = (
        "CROSS_FEED_DISAGREEMENT"  # Bit 7: Consensus divergence between redundant feeds
    )
    MALFORMED = "MALFORMED"  # Bit 8: Malformed message payload or invalid field format
    CIRCUIT_FILTER_BREACH = "CIRCUIT_FILTER_BREACH"  # Bit 9: NSE/BSE daily price band limit breach (+/- 10%)
    VOLATILITY_INTERRUPTION = "VOLATILITY_INTERRUPTION"  # Bit 10: Deutsche Boerse dynamic price corridor halt (+/- 5%)
    SPECIAL_QUOTE_INDICATION = "SPECIAL_QUOTE_INDICATION"  # Bit 11: Tokyo Stock Exchange Tokuhai quote indication (+/- 8%)
    TS_IMPLAUSIBLE = (
        "TS_IMPLAUSIBLE"  # Bit 12: exchange timestamp implausibly ahead of receive time
    )
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

    def to_dict(self) -> Dict[str, Any]:
        """Convert CanonicalEvent to a serializable dictionary."""
        return {
            "event_id": self.event_id,
            "instrument_id": self.instrument_id,
            "event_type": self.event_type.value
            if hasattr(self.event_type, "value")
            else str(self.event_type),
            "exchange_timestamp": self.exchange_timestamp,
            "receive_timestamp": self.receive_timestamp,
            "processing_timestamp": self.processing_timestamp,
            "source": self.source,
            "sequence_number": self.sequence_number,
            "price": self.price,
            "quantity": self.quantity,
            "bid_price": self.bid_price,
            "bid_size": self.bid_size,
            "ask_price": self.ask_price,
            "ask_size": self.ask_size,
            "quality_status": self.quality_status.value
            if hasattr(self.quality_status, "value")
            else str(self.quality_status),
            "reasons": list(self.reasons),
            "raw_id": self.raw_id,
            "venue": self.venue,
            "currency": self.currency,
            "asset_class": self.asset_class.value
            if hasattr(self.asset_class, "value")
            else str(self.asset_class),
        }

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalEvent:
        """Construct a CanonicalEvent from a dictionary with strict type coercion and safety guards."""
        raw_et = data.get("event_type", "TRADE")
        try:
            et = EventType(raw_et)
        except ValueError:
            et = EventType.UNKNOWN

        raw_qs = data.get("quality_status", "VALID")
        try:
            qs = QualityStatus(raw_qs)
        except ValueError:
            qs = QualityStatus.INVALID

        raw_ac = data.get("asset_class", "EQUITY")
        try:
            ac = AssetClass(raw_ac)
        except ValueError:
            ac = AssetClass.EQUITY

        return cls(
            event_id=str(data.get("event_id", "")),
            instrument_id=str(data.get("instrument_id", "")),
            event_type=et,
            exchange_timestamp=float(data.get("exchange_timestamp", 0.0)),
            receive_timestamp=float(data.get("receive_timestamp", 0.0)),
            processing_timestamp=float(data.get("processing_timestamp", 0.0)),
            source=str(data.get("source", "UNKNOWN")),
            sequence_number=int(data["sequence_number"])
            if data.get("sequence_number") is not None
            else None,
            price=float(data["price"]) if data.get("price") is not None else None,
            quantity=float(data["quantity"])
            if data.get("quantity") is not None
            else None,
            bid_price=float(data["bid_price"])
            if data.get("bid_price") is not None
            else None,
            bid_size=float(data["bid_size"])
            if data.get("bid_size") is not None
            else None,
            ask_price=float(data["ask_price"])
            if data.get("ask_price") is not None
            else None,
            ask_size=float(data["ask_size"])
            if data.get("ask_size") is not None
            else None,
            quality_status=qs,
            reasons=list(data.get("reasons", [])),
            raw_id=str(data.get("raw_id", "")),
            venue=str(data.get("venue", "XNAS")),
            currency=str(data.get("currency", "USD")),
            asset_class=ac,
        )

    @classmethod
    def from_json(cls, json_str: str) -> CanonicalEvent:
        """Construct a CanonicalEvent from a JSON string."""
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# Specialized Canonical Event Subtypes (Phase 5)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MarketEvent:
    """Base specialized market event container with nanosecond timestamp access."""

    event_id: str
    instrument_id: str
    event_type: EventType
    exchange_timestamp: float
    receive_timestamp: float
    source: str
    sequence_number: Optional[int] = None
    venue: str = "XNAS"
    quality_status: QualityStatus = QualityStatus.VALID
    reasons: List[str] = field(default_factory=list)

    @property
    def exchange_timestamp_ns(self) -> int:
        """Exchange clock in integer nanoseconds since Unix epoch."""
        return int(self.exchange_timestamp * 1_000_000_000)

    @property
    def receive_timestamp_ns(self) -> int:
        """Gateway ingress clock in integer nanoseconds since Unix epoch."""
        return int(self.receive_timestamp * 1_000_000_000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "instrument_id": self.instrument_id,
            "event_type": self.event_type.value
            if hasattr(self.event_type, "value")
            else str(self.event_type),
            "exchange_timestamp": self.exchange_timestamp,
            "receive_timestamp": self.receive_timestamp,
            "source": self.source,
            "sequence_number": self.sequence_number,
            "venue": self.venue,
            "quality_status": self.quality_status.value
            if hasattr(self.quality_status, "value")
            else str(self.quality_status),
            "reasons": list(self.reasons),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass(slots=True)
class TradeEvent(MarketEvent):
    """Execution trade tick event with price, quantity, and aggressor side."""

    price: float = 0.0
    quantity: float = 0.0
    side: str = "UNKNOWN"  # "BUY", "SELL", "UNKNOWN"
    trade_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = MarketEvent.to_dict(self)
        d.update(
            {
                "price": self.price,
                "quantity": self.quantity,
                "side": self.side,
                "trade_id": self.trade_id,
            }
        )
        return d


@dataclass(slots=True)
class QuoteEvent(MarketEvent):
    """Top-of-book (Level 1) BBO quote event with bid/ask prices and sizes."""

    bid_price: float = 0.0
    bid_size: float = 0.0
    ask_price: float = 0.0
    ask_size: float = 0.0

    @property
    def spread(self) -> float:
        """Bid-Ask spread in quote currency units."""
        return self.ask_price - self.bid_price

    @property
    def mid_price(self) -> float:
        """Arithmetic midpoint price."""
        return (self.bid_price + self.ask_price) / 2.0

    def to_dict(self) -> Dict[str, Any]:
        d = MarketEvent.to_dict(self)
        d.update(
            {
                "bid_price": self.bid_price,
                "bid_size": self.bid_size,
                "ask_price": self.ask_price,
                "ask_size": self.ask_size,
                "spread": self.spread,
                "mid_price": self.mid_price,
            }
        )
        return d


@dataclass(slots=True)
class BookEvent(MarketEvent):
    """Market-By-Order (Level 3) individual order book action."""

    order_id: str = ""
    side: str = "BUY"  # "BUY" or "SELL"
    price: float = 0.0
    size: float = 0.0
    action: str = "ADD"  # "ADD", "MODIFY", "CANCEL", "EXECUTE"

    def to_dict(self) -> Dict[str, Any]:
        d = MarketEvent.to_dict(self)
        d.update(
            {
                "order_id": self.order_id,
                "side": self.side,
                "price": self.price,
                "size": self.size,
                "action": self.action,
            }
        )
        return d


@dataclass(slots=True)
class DepthEvent(MarketEvent):
    """Consolidated aggregated market depth (Level 2) snapshot or delta."""

    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    is_snapshot: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = MarketEvent.to_dict(self)
        d.update(
            {
                "bids": self.bids,
                "asks": self.asks,
                "is_snapshot": self.is_snapshot,
            }
        )
        return d


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError, OverflowError):
        return default


def _safe_int(val: Any) -> Optional[int]:
    try:
        if isinstance(val, float) and not math.isfinite(val):
            return None
        return int(val) % (2**64)
    except (ValueError, TypeError, OverflowError):
        return None


def safe_parse_market_event(data: Any) -> Tuple[Optional[MarketEvent], List[str]]:
    """Fail-safe event parser.

    Guarantees that NO malformed payload (regardless of truncation, invalid types,
    NaNs, infinities, or extreme numbers) will raise an unhandled exception or crash the process.

    Returns:
        (event_or_none, list_of_validation_errors)
    """
    errors: List[str] = []
    if not isinstance(data, dict):
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
                if not isinstance(data, dict):
                    return None, ["Payload is not a valid JSON dictionary"]
            except Exception as exc:
                return None, [f"JSON decoding error: {exc}"]
        else:
            return None, ["Payload must be a dict, json string, or bytes"]

    # Validate essential identifiers
    instrument_id = str(data.get("instrument_id", data.get("symbol", ""))).strip()
    if not instrument_id:
        errors.append("Missing required field 'instrument_id'")

    source = str(data.get("source", data.get("exchange", "UNKNOWN"))).strip()
    event_id = str(data.get("event_id", f"{source}_{time.time_ns()}"))

    # Validate timestamps
    try:
        ex_ts = float(data.get("exchange_timestamp", data.get("ts", time.time())))
        if not math.isfinite(ex_ts) or ex_ts < 0:
            ex_ts = time.time()
            errors.append("Invalid or non-finite exchange_timestamp")
    except (ValueError, TypeError, OverflowError):
        ex_ts = time.time()
        errors.append("Malformed exchange_timestamp type")

    try:
        rx_ts = float(data.get("receive_timestamp", time.time()))
        if not math.isfinite(rx_ts) or rx_ts < 0:
            rx_ts = time.time()
            errors.append("Invalid or non-finite receive_timestamp")
    except (ValueError, TypeError, OverflowError):
        rx_ts = time.time()
        errors.append("Malformed receive_timestamp type")

    # Sequence number validation (with uint64 rollover guard)
    seq = _safe_int(data.get("sequence_number", data.get("seq")))
    if data.get("sequence_number") is not None and seq is None:
        errors.append("Malformed sequence_number type")

    raw_et = str(data.get("event_type", data.get("type", "TRADE"))).upper()
    venue = str(data.get("venue", "XNAS"))

    status = QualityStatus.INVALID if errors else QualityStatus.VALID

    # Dispatch to specialized event subtype
    try:
        if raw_et in ("QUOTE", "BBO"):
            bp = _safe_float(data.get("bid_price", data.get("bid", 0.0)))
            bs = _safe_float(data.get("bid_size", data.get("bsize", 0.0)))
            ap = _safe_float(data.get("ask_price", data.get("ask", 0.0)))
            asize = _safe_float(data.get("ask_size", data.get("asize", 0.0)))
            ev = QuoteEvent(
                event_id=event_id,
                instrument_id=instrument_id,
                event_type=EventType.QUOTE,
                exchange_timestamp=ex_ts,
                receive_timestamp=rx_ts,
                source=source,
                sequence_number=seq,
                venue=venue,
                bid_price=bp,
                bid_size=bs,
                ask_price=ap,
                ask_size=asize,
                quality_status=status,
                reasons=list(errors),
            )
            return ev, errors

        elif raw_et == "DEPTH":
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])
            clean_bids = []
            clean_asks = []
            if isinstance(raw_bids, list):
                for b in raw_bids:
                    if isinstance(b, (list, tuple)) and len(b) >= 2:
                        p_val = _safe_float(b[0], -1.0)
                        s_val = _safe_float(b[1], -1.0)
                        if p_val >= 0 and s_val >= 0:
                            clean_bids.append((p_val, s_val))
            if isinstance(raw_asks, list):
                for a in raw_asks:
                    if isinstance(a, (list, tuple)) and len(a) >= 2:
                        p_val = _safe_float(a[0], -1.0)
                        s_val = _safe_float(a[1], -1.0)
                        if p_val >= 0 and s_val >= 0:
                            clean_asks.append((p_val, s_val))
            ev = DepthEvent(
                event_id=event_id,
                instrument_id=instrument_id,
                event_type=EventType.DEPTH,
                exchange_timestamp=ex_ts,
                receive_timestamp=rx_ts,
                source=source,
                sequence_number=seq,
                venue=venue,
                bids=clean_bids,
                asks=clean_asks,
                is_snapshot=bool(data.get("is_snapshot", True)),
                quality_status=status,
                reasons=list(errors),
            )
            return ev, errors

        else:
            p = _safe_float(data.get("price", 0.0))
            q = _safe_float(data.get("quantity", data.get("size", 0.0)))
            side = str(data.get("side", "UNKNOWN")).upper()
            ev = TradeEvent(
                event_id=event_id,
                instrument_id=instrument_id,
                event_type=EventType.TRADE,
                exchange_timestamp=ex_ts,
                receive_timestamp=rx_ts,
                source=source,
                sequence_number=seq,
                venue=venue,
                price=p,
                quantity=q,
                side=side,
                trade_id=str(data.get("trade_id", ""))
                if data.get("trade_id")
                else None,
                quality_status=status,
                reasons=list(errors),
            )
            return ev, errors
    except Exception as exc:
        return None, [f"Unexpected parse exception safely intercepted: {exc}"]
