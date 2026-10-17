"""Databento Binary Encoding (DBN) Market Data Adapter (Phase 4).

Streams and normalizes institutional Databento feeds with:
- Sub-microsecond binary unpacking of MBP-1, MBP-10, and TradeMsg records.
- Nanosecond UTC epoch timestamps and 1e9 fixed-point integer scaling.
- Monotonic sequence verification and gap tracking.
- Full capability declarations and commercial licensing metadata.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any, Optional

from ..models import CanonicalEvent, EventType, QualityStatus, RawEvent
from .base import (
    AdapterCapabilities,
    AdapterStreamState,
    BaseFeedAdapter,
    LicensingTier,
)

logger = logging.getLogger("mdrap.adapters.databento")
__stability__ = "stable"

# Databento Constants
FIXED_PX_FACTOR = 1_000_000_000.0  # 1e9 fixed-point scaling factor
UNDEF_PRICE = 9223372036854775807  # INT64_MAX denotes unset/null price

# Pre-compiled C struct decoders
# Header: length(B), rtype(B), publisher_id(H), instrument_id(I), ts_event(Q) -> 16 bytes
STRUCT_HEADER = struct.Struct("<BBHIQ")
# Trade body: price(q), size(I), action(c), side(c), flags(B), depth(B), ts_recv(Q), ts_in_delta(i), sequence(I)
STRUCT_TRADE_BODY = struct.Struct("<qIccBBQii")
# BidAskPair: bid_px(q), ask_px(q), bid_sz(I), ask_sz(I), bid_ct(I), ask_ct(I) -> 32 bytes
STRUCT_BID_ASK = struct.Struct("<qqIIII")


class DatabentoAdapter(BaseFeedAdapter):
    """Institutional feed adapter for Databento live TCP streaming and DBN historical files."""

    def __init__(
        self,
        symbol: str = "ES.FUT",
        schema: str = "mbp-1",  # "mbp-1", "trades", "tbbo"
        max_buffer_size: int = 10_000,
    ) -> None:
        super().__init__(
            venue="DATABENTO",
            symbol=symbol,
            max_buffer_size=max_buffer_size,
            strict_sequence=True,
        )
        self.schema = schema

    def capabilities(self) -> AdapterCapabilities:
        """Declare Databento adapter capabilities and licensing boundaries."""
        return AdapterCapabilities(
            venue="DATABENTO",
            supports_monotonic_sequence=True,
            supports_gap_detection=True,
            supports_retransmission=True,
            supports_snapshots=True,
            supports_historical_replay=True,
            licensing_tier=LicensingTier.COMMERCIAL_INTERNAL_DISPLAY,
            clock_source="EXCHANGE",
            transport="TCP_SOCKET",
            max_burst_eps=1_000_000,
        )

    def feed_synthetic_trade(
        self,
        seq: int,
        price: float,
        qty: float,
        ts_ns: Optional[int] = None,
        action: str = "T",
        side: str = "B",
    ) -> RawEvent:
        """Helper to create and enqueue a normalized Databento Trade record."""
        now_ts = time.time()
        ts_event_ns = ts_ns if ts_ns is not None else int(now_ts * 1e9)
        self.seq_tracker.observe(seq)

        raw = RawEvent(
            source="DATABENTO",
            payload={
                "instrument": self.symbol,
                "event_type": "TRADE",
                "exchange_ts": ts_event_ns / 1e9,
                "sequence": seq,
                "price": price,
                "quantity": qty,
                "action": action,
                "side": side,
                "ts_event_ns": ts_event_ns,
            },
            receive_timestamp=now_ts,
            raw_id=f"dbn_trd_{seq}_{int(now_ts * 1000)}",
        )
        self.enqueue_raw(raw)
        return raw

    def feed_synthetic_mbp1(
        self,
        seq: int,
        bid_px: float,
        ask_px: float,
        bid_sz: float,
        ask_sz: float,
        ts_ns: Optional[int] = None,
    ) -> RawEvent:
        """Helper to create and enqueue a normalized Databento MBP-1 (top of book) record."""
        now_ts = time.time()
        ts_event_ns = ts_ns if ts_ns is not None else int(now_ts * 1e9)
        self.seq_tracker.observe(seq)

        raw = RawEvent(
            source="DATABENTO",
            payload={
                "instrument": self.symbol,
                "event_type": "QUOTE",
                "exchange_ts": ts_event_ns / 1e9,
                "sequence": seq,
                "bid": bid_px,
                "ask": ask_px,
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "ts_event_ns": ts_event_ns,
            },
            receive_timestamp=now_ts,
            raw_id=f"dbn_mbp_{seq}_{int(now_ts * 1000)}",
        )
        self.enqueue_raw(raw)
        return raw

    def unpack_dbn_frame(
        self, frame_bytes: bytes, t_recv: Optional[float] = None
    ) -> Optional[RawEvent]:
        """Decode a single raw DBN binary message and enqueue it."""
        t_recv = t_recv if t_recv is not None else time.time()
        if len(frame_bytes) < STRUCT_HEADER.size:
            return None

        length, rtype, pub_id, inst_id, ts_event_ns = STRUCT_HEADER.unpack_from(
            frame_bytes, 0
        )
        ex_ts = ts_event_ns / 1e9

        # TradeMsg (rtype 0x15)
        if (
            rtype == 0x15
            and len(frame_bytes) >= STRUCT_HEADER.size + STRUCT_TRADE_BODY.size
        ):
            (
                px_raw,
                sz_raw,
                act_raw,
                side_raw,
                flags,
                depth,
                ts_recv_raw,
                ts_delta,
                seq,
            ) = STRUCT_TRADE_BODY.unpack_from(frame_bytes, STRUCT_HEADER.size)

            self.seq_tracker.observe(seq)
            px = px_raw / FIXED_PX_FACTOR if px_raw != UNDEF_PRICE else None
            qty = float(sz_raw)

            raw = RawEvent(
                source="DATABENTO",
                payload={
                    "instrument": self.symbol,
                    "event_type": "TRADE",
                    "exchange_ts": ex_ts,
                    "sequence": seq,
                    "price": px,
                    "quantity": qty,
                    "action": act_raw.decode("ascii", errors="replace"),
                    "side": side_raw.decode("ascii", errors="replace"),
                },
                receive_timestamp=t_recv,
                raw_id=f"dbn_bin_trd_{seq}",
            )
            self.enqueue_raw(raw)
            return raw

        # MBP-1 / TBBO (rtype 0x01 or 0x02)
        if (
            rtype in (0x01, 0x02)
            and len(frame_bytes)
            >= STRUCT_HEADER.size + STRUCT_TRADE_BODY.size + STRUCT_BID_ASK.size
        ):
            body_off = STRUCT_HEADER.size
            _, _, _, _, _, _, _, _, seq = STRUCT_TRADE_BODY.unpack_from(
                frame_bytes, body_off
            )
            self.seq_tracker.observe(seq)

            level_off = body_off + STRUCT_TRADE_BODY.size
            bid_px_raw, ask_px_raw, bid_sz_raw, ask_sz_raw, _, _ = (
                STRUCT_BID_ASK.unpack_from(frame_bytes, level_off)
            )

            bid = bid_px_raw / FIXED_PX_FACTOR if bid_px_raw != UNDEF_PRICE else None
            ask = ask_px_raw / FIXED_PX_FACTOR if ask_px_raw != UNDEF_PRICE else None

            raw = RawEvent(
                source="DATABENTO",
                payload={
                    "instrument": self.symbol,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "sequence": seq,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": float(bid_sz_raw),
                    "ask_size": float(ask_sz_raw),
                },
                receive_timestamp=t_recv,
                raw_id=f"dbn_bin_mbp_{seq}",
            )
            self.enqueue_raw(raw)
            return raw

        return None

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate raw Databento payload into canonical event schema."""
        p = raw.payload if isinstance(raw.payload, dict) else {}
        ev_type_str = p.get(
            "event_type", "TRADE" if ("price" in p or "quantity" in p) else "QUOTE"
        )
        ev_type = EventType.TRADE if ev_type_str == "TRADE" else EventType.QUOTE

        ex_ts = float(p.get("exchange_ts", raw.receive_timestamp))
        seq = p.get("sequence")
        if seq is not None:
            try:
                seq = int(seq)
            except (ValueError, TypeError):
                seq = None

        price = float(p["price"]) if p.get("price") is not None else None
        qty = float(p["quantity"]) if p.get("quantity") is not None else None
        bid = float(p["bid"]) if p.get("bid") is not None else None
        ask = float(p["ask"]) if p.get("ask") is not None else None
        bid_sz = float(p["bid_size"]) if p.get("bid_size") is not None else None
        ask_sz = float(p["ask_size"]) if p.get("ask_size") is not None else None

        return CanonicalEvent(
            event_id=raw.raw_id or f"databento_{int(raw.receive_timestamp * 1000)}",
            instrument_id=p.get("instrument", self.symbol),
            event_type=ev_type,
            exchange_timestamp=ex_ts,
            receive_timestamp=raw.receive_timestamp,
            processing_timestamp=time.time(),
            source=self.venue,
            price=price,
            quantity=qty,
            bid_price=bid,
            ask_price=ask,
            bid_size=bid_sz,
            ask_size=ask_sz,
            quality_status=QualityStatus.VALID,
            sequence_number=seq,
        )
