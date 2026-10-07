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
import math
import os
import secrets
import time

from .models import CanonicalEvent, EventType, RawEvent

__stability__ = "stable"

# Fast lock-free monotonic counter with random 64-bit run identifier
# Prevents primary key collisions on process restart (S0 Card #1)
_RUN_ID: str = os.environ.get("MDRAP_RUN_ID", secrets.token_hex(8))

_SOURCES = ["FEEDX", "FEEDY", "FEEDZ", "SOURCEA", "SOURCEB", "SOURCEC"]
_INSTRUMENTS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM"]


class SchemaError(Exception):
    """Raised when an incoming raw vendor payload violates structural schema constraints."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# Required fields for TRADE events: pricing, volume, exchange clock, and sequence ID
REQUIRED_TRADE_FIELDS = (
    "instrument",
    "event_type",
    "price",
    "quantity",
)

# Required fields for QUOTE events: top-of-book bid/ask prices, exchange clock, and sequence ID
REQUIRED_QUOTE_FIELDS = (
    "instrument",
    "event_type",
    "bid",
    "ask",
)

_EVENT_TYPE_TRADE = EventType.TRADE
_EVENT_TYPE_QUOTE = EventType.QUOTE


class Gateway:
    """Instance-scoped gateway for ingesting and normalizing vendor market data payloads."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id: str = run_id or secrets.token_hex(8)
        self.id_counter = itertools.count(1)
        self.symbol_metadata_cache: dict[str, tuple[str, str]] = {}
        self.source_id_map: dict[str, int] = {s: i for i, s in enumerate(_SOURCES)}
        self.instrument_id_map: dict[str, int] = {inst: i for i, inst in enumerate(_INSTRUMENTS)}

    def next_raw_id(self) -> str:
        return f"raw-{self.run_id}-{next(self.id_counter)}"

    def next_event_id(self) -> str:
        return f"evt-{self.run_id}-{next(self.id_counter)}"

    def ingest(self, raw: RawEvent) -> RawEvent:
        """Gateway ingress step: assign unique raw ID and ingress timestamp if absent."""
        if not raw.raw_id:
            raw.raw_id = self.next_raw_id()
        if not raw.receive_timestamp:
            raw.receive_timestamp = time.time()
        return raw

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Map and parse a vendor RawEvent payload into a standardized CanonicalEvent."""
        p = raw.payload
        if not isinstance(p, dict):
            raise SchemaError(f"payload must be a dict, got {type(p).__name__}")

        event_type_raw = p.get("event_type")
        if event_type_raw not in ("TRADE", "QUOTE"):
            raise SchemaError(f"unknown or missing event_type: {event_type_raw!r}")

        required = (
            REQUIRED_TRADE_FIELDS if event_type_raw == "TRADE" else REQUIRED_QUOTE_FIELDS
        )
        for f in required:
            if f not in p:
                missing = [req for req in required if req not in p]
                raise SchemaError(f"missing fields: {missing}")

        # exchange_ts: use receive_timestamp as fallback for unsequenced feeds
        clock_source = "EXCHANGE"
        exchange_ts = p.get("exchange_ts")
        if exchange_ts is None:
            exchange_ts = raw.receive_timestamp
            clock_source = "GATEWAY_RECV"
        elif isinstance(exchange_ts, bool) or not isinstance(exchange_ts, (int, float)):
            raise SchemaError(f"exchange_ts not numeric: {exchange_ts!r}")
        elif (isinstance(exchange_ts, int) and exchange_ts.bit_length() > 1024) or not math.isfinite(exchange_ts):
            raise SchemaError(f"exchange_ts not finite: {exchange_ts!r}")

        # sequence: optional for feeds that don't provide monotonic sequence numbers (bounded to signed int64)
        sequence = p.get("sequence")
        if sequence is not None:
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise SchemaError(f"sequence not an int: {sequence!r}")
            if sequence < -9223372036854775808 or sequence > 9223372036854775807:
                raise SchemaError(f"sequence integer out of int64 range: {sequence!r}")

        instrument = p["instrument"]
        if not isinstance(instrument, str) or not instrument:
            raise SchemaError("instrument missing or not a string")

        s_id = self.source_id_map.get(raw.source)
        if s_id is None:
            s_id = self.source_id_map[raw.source] = len(self.source_id_map)
        i_id = self.instrument_id_map.get(instrument)
        if i_id is None:
            i_id = self.instrument_id_map[instrument] = len(self.instrument_id_map)

        event = CanonicalEvent(
            event_id=self.next_event_id(),
            instrument_id=instrument,
            event_type=_EVENT_TYPE_TRADE
            if event_type_raw == "TRADE"
            else _EVENT_TYPE_QUOTE,
            exchange_timestamp=float(exchange_ts),
            receive_timestamp=raw.receive_timestamp,
            processing_timestamp=0.0,  # Populated after quality evaluation
            source=raw.source,
            sequence_number=sequence,
            raw_id=raw.raw_id,
            source_id=s_id,
            instrument_id_int=i_id,
        )
        event.clock_source = clock_source
        if p.get("is_simulated") or "SIM" in raw.source:
            event.source_kind = "SIMULATED"
        elif "REPLAY" in raw.source:
            event.source_kind = "REPLAY"
        else:
            event.source_kind = str(p.get("source_kind", "LIVE"))

        meta = self.symbol_metadata_cache.get(instrument)
        if meta is None:
            try:
                from .symbology import resolve_symbol

                sym_info = resolve_symbol(instrument)
                meta = (sym_info.venue_mic, sym_info.currency)
            except Exception:
                meta = ("XNAS", "USD")
            self.symbol_metadata_cache[instrument] = meta

        event.venue = p.get("venue") or meta[0]
        event.currency = p.get("currency") or meta[1]

        if event_type_raw == "TRADE":
            price, qty = p["price"], p["quantity"]
            if (
                isinstance(price, bool)
                or not isinstance(price, (int, float))
                or (isinstance(price, int) and price.bit_length() > 1024)
                or not math.isfinite(price)
                or price <= 0
            ):
                raise SchemaError(f"invalid price: {price!r}")
            if (
                isinstance(qty, bool)
                or not isinstance(qty, (int, float))
                or (isinstance(qty, int) and qty.bit_length() > 1024)
                or not math.isfinite(qty)
                or qty <= 0
            ):
                raise SchemaError(f"invalid quantity: {qty!r}")
            event.price = float(price)
            event.quantity = float(qty)
        else:
            bid, ask = p["bid"], p["ask"]
            if (
                isinstance(bid, bool)
                or isinstance(ask, bool)
                or not isinstance(bid, (int, float))
                or not isinstance(ask, (int, float))
                or (isinstance(bid, int) and bid.bit_length() > 1024)
                or (isinstance(ask, int) and ask.bit_length() > 1024)
                or not math.isfinite(bid)
                or not math.isfinite(ask)
            ):
                raise SchemaError("bid/ask not numeric or not finite")
            event.bid_price = float(bid)
            event.ask_price = float(ask)
            bid_sz = p.get("bid_size", 0) or 0
            ask_sz = p.get("ask_size", 0) or 0
            if isinstance(bid_sz, bool) or not isinstance(bid_sz, (int, float)):
                raise SchemaError("bid_size not numeric")
            if isinstance(ask_sz, bool) or not isinstance(ask_sz, (int, float)):
                raise SchemaError("ask_size not numeric")
            event.bid_size = float(bid_sz)
            event.ask_size = float(ask_sz)

        return event


_DEFAULT_GATEWAY = Gateway(run_id=_RUN_ID)
_gateway_id_counter = _DEFAULT_GATEWAY.id_counter
_symbol_metadata_cache = _DEFAULT_GATEWAY.symbol_metadata_cache
_SOURCE_ID_MAP = _DEFAULT_GATEWAY.source_id_map
_INSTRUMENT_ID_MAP = _DEFAULT_GATEWAY.instrument_id_map


def reset_gateway_ids(run_id: str | None = None) -> None:
    """Reset run_id and counter (for test reproducibility or isolation)."""
    global _RUN_ID, _DEFAULT_GATEWAY, _gateway_id_counter, _symbol_metadata_cache
    _RUN_ID = run_id or secrets.token_hex(8)
    _DEFAULT_GATEWAY = Gateway(run_id=_RUN_ID)
    _gateway_id_counter = _DEFAULT_GATEWAY.id_counter
    _symbol_metadata_cache = _DEFAULT_GATEWAY.symbol_metadata_cache


def ingest(raw: RawEvent) -> RawEvent:
    return _DEFAULT_GATEWAY.ingest(raw)


def normalize(raw: RawEvent) -> CanonicalEvent:
    return _DEFAULT_GATEWAY.normalize(raw)

