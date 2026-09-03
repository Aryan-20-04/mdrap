"""
Feed Gateway (ingestion) + Normalization Engine.

Combined into one module for the MVP: the gateway assigns an internal
id and stamps timestamps, then normalizer.py maps the raw vendor
payload onto the canonical event model. A production build would split
these across separate services connected by the streaming bus; here
they run as plain function calls so the baseline pipeline has no
network/broker overhead to benchmark against.
"""
from __future__ import annotations

import itertools
import time
from typing import Optional

from models import CanonicalEvent, EventType, RawEvent, Reason

_gateway_id_counter = itertools.count(1)


class SchemaError(Exception):
    """Raised when a raw payload cannot be normalized at all."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def ingest(raw: RawEvent) -> RawEvent:
    """Gateway step: stamp receive time if not already set, assign a
    raw_id if missing. Kept trivial on purpose -- structural validation
    happens in normalize(), classification happens in the quality engine."""
    if not raw.raw_id:
        raw.raw_id = f"raw-{next(_gateway_id_counter)}"
    if not raw.receive_timestamp:
        raw.receive_timestamp = time.time()
    return raw


REQUIRED_TRADE_FIELDS = ("instrument", "event_type", "exchange_ts", "sequence", "price", "quantity")
REQUIRED_QUOTE_FIELDS = ("instrument", "event_type", "exchange_ts", "sequence", "bid", "ask")


def normalize(raw: RawEvent) -> CanonicalEvent:
    """Map a vendor payload to the canonical event model.

    Raises SchemaError for payloads that are missing required fields or
    have the wrong type -- the quality engine turns that into an
    INVALID/SCHEMA_VIOLATION event rather than crashing the pipeline.
    """
    p = raw.payload
    event_type_raw = p.get("event_type")
    if event_type_raw not in ("TRADE", "QUOTE"):
        raise SchemaError(f"unknown or missing event_type: {event_type_raw!r}")

    required = REQUIRED_TRADE_FIELDS if event_type_raw == "TRADE" else REQUIRED_QUOTE_FIELDS
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
        processing_timestamp=0.0,  # filled in after quality checks
        source=raw.source,
        sequence_number=sequence,
        raw_id=raw.raw_id,
    )

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
