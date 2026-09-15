"""Feed Gateway (Ingestion) and Normalization Engine.

This module forms the front-line ingress interface for MDRAP. It accepts heterogeneous
vendor payloads (represented as RawEvent), stamps gateway wall-clock receipt timestamps,
generates monotonic identifiers, and maps the payload into a strictly-typed CanonicalEvent.

Architectural Context (MDRAP Spec §11 & §26):
  1. Zero-Allocation Fast Identification:
     Event and raw IDs use `itertools.count(1)` rather than `uuid.uuid4()`. Python's
     UUID generation incurs operating system entropy syscalls and string parsing, which
     creates significant CPU jitter on multi-million tick-per-second workloads. Monotonic
     integer IDs formatted as `evt-N` provide lock-free, zero-jitter generation.
  2. Structural Schema Enforcement:
     If a payload lacks mandatory fields or contains invalid numeric types, `SchemaError`
     is raised. The orchestrator catches this error and generates an `INVALID` event tagged
     with `Reason.SCHEMA_VIOLATION`, ensuring that malformed payloads are preserved in
     quarantine for forensic auditing rather than crashing the pipeline or being silently lost.
  3. Global Symbology & Venue Tagging:
     Resolves instrument tickers to ISO 10383 Market Identifier Codes (MIC) and ISO 4217
     currency codes via the symbology directory.
"""

from __future__ import annotations

import itertools
import time

from models import CanonicalEvent, EventType, RawEvent

# Fast lock-free monotonic counter for hot-path ID generation
_gateway_id_counter = itertools.count(1)


class SchemaError(Exception):
    """Raised when an incoming raw vendor payload violates structural schema constraints."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def ingest(raw: RawEvent) -> RawEvent:
    """Gateway ingress step: assign unique raw ID and ingress timestamp if absent.

    Structural validation is deliberately deferred to `normalize()` so that raw
    payloads can be written ahead to the archive storage in their exact received state.
    """
    if not raw.raw_id:
        raw.raw_id = f"raw-{next(_gateway_id_counter)}"
    if not raw.receive_timestamp:
        raw.receive_timestamp = time.time()
    return raw


# Required fields for TRADE events: pricing, volume, exchange clock, and sequence ID
REQUIRED_TRADE_FIELDS = (
    "instrument",
    "event_type",
    "exchange_ts",
    "sequence",
    "price",
    "quantity",
)

# Required fields for QUOTE events: top-of-book bid/ask prices, exchange clock, and sequence ID
REQUIRED_QUOTE_FIELDS = (
    "instrument",
    "event_type",
    "exchange_ts",
    "sequence",
    "bid",
    "ask",
)


def normalize(raw: RawEvent) -> CanonicalEvent:
    """Map and parse a vendor RawEvent payload into a standardized CanonicalEvent.

    Raises:
        SchemaError: If required fields are missing, event_type is unrecognized,
                     or numeric fields cannot be cast to floating point numbers.
    """
    p = raw.payload
    event_type_raw = p.get("event_type")
    if event_type_raw not in ("TRADE", "QUOTE"):
        raise SchemaError(f"unknown or missing event_type: {event_type_raw!r}")

    required = (
        REQUIRED_TRADE_FIELDS if event_type_raw == "TRADE" else REQUIRED_QUOTE_FIELDS
    )
    missing = [f for f in required if f not in p]
    if missing:
        raise SchemaError(f"missing fields: {missing}")

    exchange_ts = p["exchange_ts"]
    if not isinstance(exchange_ts, (int, float)):
        raise SchemaError(f"exchange_ts not numeric: {exchange_ts!r}")

    sequence = p["sequence"]
    if not isinstance(sequence, int):
        raise SchemaError(f"sequence not an int: {sequence!r}")

    instrument = p["instrument"]
    if not isinstance(instrument, str) or not instrument:
        raise SchemaError("instrument missing or not a string")

    event = CanonicalEvent(
        event_id=f"evt-{next(_gateway_id_counter)}",
        instrument_id=instrument,
        event_type=EventType(event_type_raw),
        exchange_timestamp=float(exchange_ts),
        receive_timestamp=raw.receive_timestamp,
        processing_timestamp=0.0,  # Populated after quality evaluation
        source=raw.source,
        sequence_number=sequence,
        raw_id=raw.raw_id,
    )

    try:
        from symbology import resolve_symbol

        sym_info = resolve_symbol(instrument)
        event.venue = p.get("venue") or sym_info.venue_mic
        event.currency = p.get("currency") or sym_info.currency
    except Exception:
        event.venue = p.get("venue") or "XNAS"
        event.currency = p.get("currency") or "USD"

    if event_type_raw == "TRADE":
        price, qty = p["price"], p["quantity"]
        if not isinstance(price, (int, float)) or price <= 0:
            raise SchemaError(f"invalid price: {price!r}")
        if not isinstance(qty, (int, float)) or qty <= 0:
            raise SchemaError(f"invalid quantity: {qty!r}")
        event.price = float(price)
        event.quantity = float(qty)
    else:
        bid, ask = p["bid"], p["ask"]
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
            raise SchemaError("bid/ask not numeric")
        event.bid_price = float(bid)
        event.ask_price = float(ask)
        event.bid_size = float(p.get("bid_size", 0) or 0)
        event.ask_size = float(p.get("ask_size", 0) or 0)

    return event
