"""Kraken Market Data Adapter (Phase 4).

Streams and normalizes public Kraken WebSocket feeds with:
- Message sequence verification and gap detection.
- Book snapshots and top-of-book ticker updates.
- Exponential backoff reconnection with jitter.
- Explicit capability declarations and drop accounting.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..models import CanonicalEvent, EventType, QualityStatus, RawEvent
from .base import AdapterCapabilities, AdapterStreamState, BaseFeedAdapter, LicensingTier

logger = logging.getLogger("mdrap.adapters.kraken")


class KrakenAdapter(BaseFeedAdapter):
    """Institutional adapter for Kraken spot and futures market data."""

    def __init__(
        self,
        symbol: str = "XBT/USD",
        stream_type: str = "ticker",  # "ticker", "book", "trade"
        max_buffer_size: int = 10_000,
    ) -> None:
        super().__init__(
            venue="KRAKEN",
            symbol=symbol,
            max_buffer_size=max_buffer_size,
            strict_sequence=False,
        )
        self.stream_type = stream_type

    def capabilities(self) -> AdapterCapabilities:
        """Declare Kraken adapter capabilities and licensing boundaries."""
        return AdapterCapabilities(
            venue="KRAKEN",
            supports_monotonic_sequence=True,
            supports_gap_detection=True,
            supports_retransmission=False,
            supports_snapshots=True,
            supports_historical_replay=False,
            licensing_tier=LicensingTier.PUBLIC_COMMUNITY,
            clock_source="EXCHANGE",
            transport="WEBSOCKET",
            max_burst_eps=50_000,
        )

    def parse_frame(self, data: Any, t_recv: Optional[float] = None) -> Optional[RawEvent]:
        """Parse raw incoming Kraken list-based or dict WebSocket frame and enqueue it."""
        t_recv = t_recv if t_recv is not None else time.time()

        # Handle list format: [channelID, dataPayload, channelName, pair]
        if isinstance(data, list) and len(data) >= 2:
            body = data[1]
            channel_name = str(data[2]) if len(data) > 2 else ""

            # Check sequence number if provided in list frame (e.g. sequence is at end or dict)
            seq = None
            if len(data) >= 5 and isinstance(data[-1], int):
                seq = data[-1]
            elif isinstance(body, dict) and "sequence" in body:
                try:
                    seq = int(body["sequence"])
                except (ValueError, TypeError):
                    seq = None

            if seq is not None:
                self.seq_tracker.observe(seq)

            if isinstance(body, dict):
                # 1. Book snapshot: "bs" and "as"
                if "bs" in body and "as" in body and body["bs"] and body["as"]:
                    bids = [[float(row[0]), float(row[1])] for row in body["bs"][:5]]
                    asks = [[float(row[0]), float(row[1])] for row in body["as"][:5]]
                    ex_ts = float(body["bs"][0][2]) if len(body["bs"][0]) > 2 else t_recv

                    raw = RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": self.symbol,
                            "event_type": "QUOTE",
                            "exchange_ts": ex_ts,
                            "sequence": seq,
                            "bid": bids[0][0] if bids else None,
                            "ask": asks[0][0] if asks else None,
                            "bid_size": bids[0][1] if bids else None,
                            "ask_size": asks[0][1] if asks else None,
                            "bids": bids,
                            "asks": asks,
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"kraken_book_{seq or int(t_recv * 1000)}",
                    )
                    self.enqueue_raw(raw)
                    return raw

                # 2. Ticker update: "b"=bid, "a"=ask
                if "b" in body and "a" in body:
                    bid = float(body["b"][0])
                    ask = float(body["a"][0])
                    bid_sz = float(body["b"][2]) if len(body["b"]) > 2 else None
                    ask_sz = float(body["a"][2]) if len(body["a"]) > 2 else None

                    raw = RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": self.symbol,
                            "event_type": "QUOTE",
                            "exchange_ts": t_recv,
                            "sequence": seq,
                            "bid": bid,
                            "ask": ask,
                            "bid_size": bid_sz,
                            "ask_size": ask_sz,
                            "bids": [[bid, bid_sz]] if bid is not None else [],
                            "asks": [[ask, ask_sz]] if ask is not None else [],
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"kraken_tick_{seq or int(t_recv * 1000)}",
                    )
                    self.enqueue_raw(raw)
                    return raw

            # 3. Trade messages: body is list of trades [[price, volume, time, side, ...], ...]
            if isinstance(body, list) and body and isinstance(body[0], list):
                last_raw = None
                for trd in body:
                    px = float(trd[0])
                    sz = float(trd[1])
                    trd_ts = float(trd[2]) if len(trd) > 2 else t_recv
                    raw = RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": self.symbol,
                            "event_type": "TRADE",
                            "exchange_ts": trd_ts,
                            "sequence": seq,
                            "price": px,
                            "quantity": sz,
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"kraken_trd_{int(trd_ts * 1000)}_{int(px * 100)}",
                    )
                    self.enqueue_raw(raw)
                    last_raw = raw
                return last_raw

        # Handle direct dict format (e.g. from Kraken WebSocket v2)
        if isinstance(data, dict):
            seq = data.get("sequence")
            if seq is not None:
                try:
                    seq = int(seq)
                    self.seq_tracker.observe(seq)
                except (ValueError, TypeError):
                    seq = None

            px = data.get("price")
            qty = data.get("qty") or data.get("volume")
            bid = data.get("bid")
            ask = data.get("ask")

            raw = RawEvent(
                source="KRAKEN",
                payload={
                    "instrument": self.symbol,
                    "event_type": "TRADE" if px is not None else "QUOTE",
                    "exchange_ts": float(data.get("timestamp", t_recv)),
                    "sequence": seq,
                    "price": float(px) if px is not None else None,
                    "quantity": float(qty) if qty is not None else None,
                    "bid": float(bid) if bid is not None else None,
                    "ask": float(ask) if ask is not None else None,
                },
                receive_timestamp=t_recv,
                raw_id=f"kraken_v2_{seq or int(t_recv * 1000)}",
            )
            self.enqueue_raw(raw)
            return raw

        return None

    def normalize(self, raw: RawEvent) -> CanonicalEvent:
        """Translate raw Kraken payload into canonical event schema."""
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
            event_id=raw.raw_id or f"kraken_{int(raw.receive_timestamp * 1000)}",
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
