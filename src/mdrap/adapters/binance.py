"""Binance Market Data Adapter (Phase 4).

Streams and normalizes public Binance WebSocket depth and trade feeds with:
- Synthetic timestamp fallback when exchange timestamp ('E') is omitted.
- Sequence tracking and gap detection via 'lastUpdateId' and 'u'.
- Exponential backoff reconnection with random jitter.
- Explicit capability declarations and drop accounting.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

from ..models import CanonicalEvent, EventType, QualityStatus, RawEvent
from .base import AdapterCapabilities, AdapterStreamState, BaseFeedAdapter, LicensingTier

logger = logging.getLogger("mdrap.adapters.binance")


class BinanceAdapter(BaseFeedAdapter):
    """Institutional adapter for Binance public market data."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        stream_type: str = "bookTicker",  # "bookTicker", "depth5", "trade"
        max_buffer_size: int = 10_000,
    ) -> None:
        super().__init__(
            venue="BINANCE",
            symbol=symbol.upper(),
            max_buffer_size=max_buffer_size,
            strict_sequence=False,  # Binance bookTicker may emit update IDs with skips
        )
        self.stream_type = stream_type
        self._last_update_id: Optional[int] = None

    def capabilities(self) -> AdapterCapabilities:
        """Declare Binance adapter capabilities and licensing boundaries."""
        return AdapterCapabilities(
            venue="BINANCE",
            supports_monotonic_sequence=True,
            supports_gap_detection=True,
            supports_retransmission=False,
            supports_snapshots=True,
            supports_historical_replay=False,
            licensing_tier=LicensingTier.PUBLIC_COMMUNITY,
            clock_source="EXCHANGE",
            transport="WEBSOCKET",
            max_burst_eps=100_000,
        )

    def parse_frame(self, data: dict[str, Any], t_recv: Optional[float] = None) -> Optional[RawEvent]:
        """Parse raw incoming Binance WebSocket JSON frame and enqueue it."""
        t_recv = t_recv if t_recv is not None else time.time()

        # 1. Exchange timestamp with synthetic fallback
        ex_ts = None
        if "E" in data and data["E"] is not None:
            try:
                ex_ts = float(data["E"]) / 1000.0
            except (ValueError, TypeError):
                ex_ts = None

        clock_source = "EXCHANGE" if ex_ts is not None else "GATEWAY_RECV"
        if ex_ts is None:
            ex_ts = t_recv

        # 2. Extract sequence update ID
        seq_id = data.get("u") or data.get("lastUpdateId")
        if seq_id is not None:
            try:
                seq_id = int(seq_id)
                self.seq_tracker.observe(seq_id)
                self._last_update_id = seq_id
            except (ValueError, TypeError):
                seq_id = None

        # 3. Handle bookTicker format: "b"=bid, "B"=bid_size, "a"=ask, "A"=ask_size
        if "b" in data and "a" in data:
            bid = float(data["b"]) if data.get("b") is not None else None
            ask = float(data["a"]) if data.get("a") is not None else None
            bid_size = float(data["B"]) if data.get("B") is not None else None
            ask_size = float(data["A"]) if data.get("A") is not None else None

            raw = RawEvent(
                source="BINANCE",
                payload={
                    "instrument": self.symbol,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "clock_source": clock_source,
                    "sequence": seq_id,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_size,
                    "ask_size": ask_size,
                    "bids": [[bid, bid_size]] if bid is not None else [],
                    "asks": [[ask, ask_size]] if ask is not None else [],
                },
                receive_timestamp=t_recv,
                raw_id=f"binance_{seq_id or int(t_recv * 1000)}",
            )
            self.enqueue_raw(raw)
            return raw

        # 4. Handle trade format: "p"=price, "q"=qty, "t"=trade_id
        if "p" in data and "q" in data:
            price = float(data["p"]) if data.get("p") is not None else None
            qty = float(data["q"]) if data.get("q") is not None else None
            trade_id = data.get("t", seq_id)
            if trade_id is not None:
                try:
                    trade_id = int(trade_id)
                except (ValueError, TypeError):
                    trade_id = seq_id

            raw = RawEvent(
                source="BINANCE",
                payload={
                    "instrument": self.symbol,
                    "event_type": "TRADE",
                    "exchange_ts": ex_ts,
                    "clock_source": clock_source,
                    "sequence": trade_id,
                    "price": price,
                    "quantity": qty,
                },
                receive_timestamp=t_recv,
                raw_id=f"binance_trd_{trade_id or int(t_recv * 1000)}",
            )
            self.enqueue_raw(raw)
            return raw

        # 5. Handle depth format: "bids", "asks"
        if "bids" in data and "asks" in data and data["bids"] and data["asks"]:
            bids = [[float(p), float(s)] for p, s in data["bids"][:5]]
            asks = [[float(p), float(s)] for p, s in data["asks"][:5]]
            best_bid, best_bid_sz = bids[0] if bids else (None, None)
            best_ask, best_ask_sz = asks[0] if asks else (None, None)

            raw = RawEvent(
                source="BINANCE",
                payload={
                    "instrument": self.symbol,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "clock_source": clock_source,
                    "sequence": seq_id,
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": best_bid_sz,
                    "ask_size": best_ask_sz,
                    "bids": bids,
                    "asks": asks,
                },
                receive_timestamp=t_recv,
                raw_id=f"binance_dp_{seq_id or int(t_recv * 1000)}",
            )
            self.enqueue_raw(raw)
            return raw

        return None

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate raw Binance payload into canonical event schema."""
        p = raw.payload if isinstance(raw.payload, dict) else {}
        ev_type_str = p.get("event_type", "TRADE" if ("price" in p or "quantity" in p) else "QUOTE")
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
            event_id=raw.raw_id or f"binance_{int(raw.receive_timestamp * 1000)}",
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
