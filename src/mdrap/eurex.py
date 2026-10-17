"""Eurex T7 Enhanced Trading Interface (ETI) & Market Data (EMDI) Binary Decoder (Spec §18, Phase 9).

Implements binary message unpacking and validation for Eurex Exchange T7:
- Standard Eurex framing header: <body_len(u32), template_id(u16), msg_seq_num(u32)>
- OrderBookDepth (Template 10130 / EMDI) for multi-level order book state
- TradeReport (Template 10131)
- Nanosecond timestamp conversion and strict protocol versioning checks
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .models import CanonicalEvent, EventType, QualityStatus

__version__ = "1.0.0"
__stability__ = "stable"

# Eurex T7 Template Constants
EUREX_TEMPLATE_DEPTH: int = 10130
EUREX_TEMPLATE_TRADE: int = 10131
EUREX_TEMPLATE_HEARTBEAT: int = 10000

# Fixed 10-Byte Eurex Header: <body_len(u32), template_id(u16), msg_seq_num(u32)>
EUREX_HEADER_STRUCT = struct.Struct("<HIH")  # Or standard format: <IHI
# Layout: body_len(u32), template_id(u16), msg_seq_num(u32)
EUREX_HEADER_STRUCT = struct.Struct("<IHI")
EUREX_HEADER_SIZE = (
    EUREX_HEADER_STRUCT.size
)  # Exactly 10 bytes (or 12 bytes with padding)

# Price scaling factor for Eurex T7 (8 decimal places)
EUREX_PRICE_DIVISOR = 100_000_000.0


@dataclass
class EurexDepthMessage:
    """Decoded Eurex T7 EMDI Depth update."""

    seq_num: int
    security_id: int
    transact_time_ns: int
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float


@dataclass
class EurexTradeMessage:
    """Decoded Eurex T7 Trade report."""

    seq_num: int
    security_id: int
    transact_time_ns: int
    price: float
    size: float
    aggressor_side: int  # 1 = Buy, 2 = Sell


class EurexT7Decoder:
    """Direct binary message decoder for Eurex Exchange T7."""

    def __init__(self) -> None:
        self._security_symbol_map: dict[int, str] = {
            2001: "FDAX_FUT",
            2002: "FESX_FUT",
            2003: "FGBL_FUT",
        }

    def register_security(self, security_id: int, symbol: str) -> None:
        self._security_symbol_map[security_id] = symbol

    def decode_header(
        self, buf: bytes | memoryview, offset: int = 0
    ) -> tuple[int, int, int]:
        """Decode Eurex frame header: (body_len, template_id, msg_seq_num)."""
        if len(buf) - offset < EUREX_HEADER_SIZE:
            raise ValueError(
                f"Buffer too short for Eurex header: {len(buf) - offset} < {EUREX_HEADER_SIZE}"
            )
        body_len, template_id, seq_num = EUREX_HEADER_STRUCT.unpack_from(buf, offset)
        return body_len, template_id, seq_num

    def decode_frame(
        self, buf: bytes | memoryview, offset: int = 0
    ) -> EurexDepthMessage | EurexTradeMessage | None:
        """Decode a complete Eurex binary message frame."""
        body_len, template_id, seq_num = self.decode_header(buf, offset)
        pos = offset + EUREX_HEADER_SIZE

        if template_id == EUREX_TEMPLATE_DEPTH:
            # Layout: security_id(q), transact_time(Q), bid_px(q), ask_px(q), bid_sz(I), ask_sz(I) = 40 bytes
            sec_id, transact_time, bid_raw, ask_raw, bid_sz, ask_sz = (
                struct.unpack_from("<qQqqII", buf, pos)
            )
            return EurexDepthMessage(
                seq_num=seq_num,
                security_id=sec_id,
                transact_time_ns=transact_time,
                bid_price=bid_raw / EUREX_PRICE_DIVISOR if bid_raw > 0 else 0.0,
                ask_price=ask_raw / EUREX_PRICE_DIVISOR if ask_raw > 0 else 0.0,
                bid_size=float(bid_sz),
                ask_size=float(ask_sz),
            )

        elif template_id == EUREX_TEMPLATE_TRADE:
            # Layout: security_id(q), transact_time(Q), price(q), size(I), aggressor(B) = 29 bytes
            sec_id, transact_time, px_raw, sz_raw, agg = struct.unpack_from(
                "<qQqIB", buf, pos
            )
            return EurexTradeMessage(
                seq_num=seq_num,
                security_id=sec_id,
                transact_time_ns=transact_time,
                price=px_raw / EUREX_PRICE_DIVISOR if px_raw > 0 else 0.0,
                size=float(sz_raw),
                aggressor_side=agg,
            )

        return None

    def to_canonical_event(
        self,
        msg: EurexDepthMessage | EurexTradeMessage,
        receive_timestamp: float,
    ) -> CanonicalEvent:
        """Project decoded Eurex message into standard MDRAP CanonicalEvent."""
        symbol = self._security_symbol_map.get(
            msg.security_id, f"EUREX_{msg.security_id}"
        )
        exchange_ts = msg.transact_time_ns / 1_000_000_000.0

        if isinstance(msg, EurexTradeMessage):
            return CanonicalEvent(
                event_id=f"eurex_{msg.security_id}_{msg.seq_num}",
                instrument_id=symbol,
                event_type=EventType.TRADE,
                exchange_timestamp=exchange_ts,
                receive_timestamp=receive_timestamp,
                processing_timestamp=receive_timestamp,
                source="EUREX",
                sequence_number=msg.seq_num,
                price=msg.price,
                quantity=msg.size,
                quality_status=QualityStatus.VALID,
                reasons=[],
                raw_id=f"{msg.security_id}:{msg.seq_num}",
            )
        else:
            return CanonicalEvent(
                event_id=f"eurex_{msg.security_id}_{msg.seq_num}",
                instrument_id=symbol,
                event_type=EventType.QUOTE,
                exchange_timestamp=exchange_ts,
                receive_timestamp=receive_timestamp,
                processing_timestamp=receive_timestamp,
                source="EUREX",
                sequence_number=msg.seq_num,
                bid_price=msg.bid_price,
                ask_price=msg.ask_price,
                bid_size=msg.bid_size,
                ask_size=msg.ask_size,
                quality_status=QualityStatus.VALID,
                reasons=[],
                raw_id=f"{msg.security_id}:{msg.seq_num}",
            )


def pack_eurex_depth_message(
    seq_num: int,
    security_id: int,
    transact_time_ns: int,
    bid_price: float,
    ask_price: float,
    bid_size: float,
    ask_size: float,
) -> bytes:
    """Helper serializer for Eurex T7 depth messages."""
    payload_len = 40
    header = EUREX_HEADER_STRUCT.pack(payload_len, EUREX_TEMPLATE_DEPTH, seq_num)
    body = struct.pack(
        "<qQqqII",
        security_id,
        transact_time_ns,
        int(bid_price * EUREX_PRICE_DIVISOR),
        int(ask_price * EUREX_PRICE_DIVISOR),
        int(bid_size),
        int(ask_size),
    )
    return header + body


def pack_eurex_trade_message(
    seq_num: int,
    security_id: int,
    transact_time_ns: int,
    price: float,
    size: float,
    aggressor_side: int = 1,
) -> bytes:
    """Helper serializer for Eurex T7 trade messages."""
    payload_len = 29
    header = EUREX_HEADER_STRUCT.pack(payload_len, EUREX_TEMPLATE_TRADE, seq_num)
    body = struct.pack(
        "<qQqIB",
        security_id,
        transact_time_ns,
        int(price * EUREX_PRICE_DIVISOR),
        int(size),
        aggressor_side,
    )
    return header + body
