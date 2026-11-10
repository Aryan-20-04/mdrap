"""Multi-Venue Persistent WebSocket Feed Engine for MDRAP.

Streams high-frequency, sub-millisecond market data directly over persistent
full-duplex WebSockets from global cryptocurrency venues:
  - Binance: wss://stream.binance.com:9443/ws/
  - Coinbase: wss://ws-feed.exchange.coinbase.com
  - Kraken: wss://ws.kraken.com
  - OKX: wss://ws.okx.com:8443/ws/v5/public
  - Bybit: wss://stream.bybit.com/v5/public/spot

Converts incoming frames into normalized RawEvent objects and enqueues them
into a thread-safe buffer for the synchronous pipeline and depth engines.
Features automatic exponential backoff reconnection, keepalive pings,
and graceful fallback if websocket libraries are unavailable.
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import Generator
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import itertools
import json
import logging
import math
import queue
import ssl
import threading
import time
from typing import Any, Callable
import uuid

from .live import resolve_venue_symbols
from .models import RawEvent

__stability__ = "beta"

logger = logging.getLogger("mdrap.ws_feed")

RUN_ID = uuid.uuid4().hex[:12]
_raw_counter = itertools.count(1)


def _num(x: Any) -> float | None:
    if x is None or x == "":
        return None
    val = float(x)
    return val if math.isfinite(val) else None


def _parse_iso(s: Any) -> float | None:
    if not s or not isinstance(s, str):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


# Check optional websockets dependency
try:
    import websockets

    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    websockets = None  # type: ignore


# Public WebSocket endpoints
WS_ENDPOINTS = {
    "BINANCE": "wss://stream.binance.com:9443/ws/{stream}",
    "COINBASE": "wss://ws-feed.exchange.coinbase.com",
    "KRAKEN": "wss://ws.kraken.com",
    "OKX": "wss://ws.okx.com:8443/ws/v5/public",
    "BYBIT": "wss://stream.bybit.com/v5/public/spot",
}


def parse_binance_frame(
    data: dict, canonical_sym: str, t_recv: float | None = None, wire: str | bytes = ""
) -> RawEvent | None:
    """Parse Binance @depth5 or @bookTicker WebSocket JSON frame."""
    t_recv = t_recv if t_recv is not None else time.time()
    try:
        ex_ts = (
            (_num(data["E"]) / 1000.0)
            if ("E" in data and _num(data["E"]) is not None)
            else None
        )

        # Check depth format (e.g. @depth5)
        if "bids" in data and "asks" in data and data["bids"] and data["asks"]:
            bids = [
                [_num(p), _num(s)]
                for p, s in data["bids"][:5]
                if _num(p) is not None and _num(s) is not None
            ]
            asks = [
                [_num(p), _num(s)]
                for p, s in data["asks"][:5]
                if _num(p) is not None and _num(s) is not None
            ]
            best_bid, best_bid_size = bids[0] if bids else (None, None)
            best_ask, best_ask_size = asks[0] if asks else (None, None)
            return RawEvent(
                source="BINANCE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "sequence": None,
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": best_bid_size,
                    "ask_size": best_ask_size,
                    "bids": bids,
                    "asks": asks,
                    "venue_update_id": data.get("lastUpdateId"),
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-binance-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )

        # Check bookTicker format
        if "b" in data and "a" in data:
            best_bid = _num(data["b"])
            best_bid_size = _num(data.get("B"))
            best_ask = _num(data["a"])
            best_ask_size = _num(data.get("A"))
            return RawEvent(
                source="BINANCE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "sequence": None,
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": best_bid_size,
                    "ask_size": best_ask_size,
                    "bids": [[best_bid, best_bid_size]] if best_bid is not None else [],
                    "asks": [[best_ask, best_ask_size]] if best_ask is not None else [],
                    "venue_update_id": data.get("u"),
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-binance-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )
    except Exception as exc:
        return RawEvent(
            source="BINANCE",
            payload={
                "instrument": canonical_sym,
                "is_malformed": True,
                "error": repr(exc)[:200],
            },
            receive_timestamp=t_recv,
            raw_id=f"ws-binance-err-{RUN_ID}-{next(_raw_counter)}",
            wire=wire,
        )
    return None


def parse_coinbase_frame(
    data: dict, canonical_sym: str, t_recv: float | None = None, wire: str | bytes = ""
) -> RawEvent | None:
    """Parse Coinbase ticker or snapshot/l2update WebSocket frame."""
    t_recv = t_recv if t_recv is not None else time.time()
    try:
        msg_type = data.get("type")
        ex_ts = _parse_iso(data.get("time"))
        seq = (
            int(data["sequence"])
            if ("sequence" in data and _num(data["sequence"]) is not None)
            else None
        )

        if msg_type == "ticker":
            bid = _num(data.get("best_bid"))
            ask = _num(data.get("best_ask"))
            bid_s = _num(data.get("best_bid_size"))
            ask_s = _num(data.get("best_ask_size"))
            return RawEvent(
                source="COINBASE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "sequence": seq,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_s,
                    "ask_size": ask_s,
                    "bids": [[bid, bid_s]] if bid is not None else [],
                    "asks": [[ask, ask_s]] if ask is not None else [],
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-coinbase-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )
        elif msg_type == "snapshot" and "bids" in data and "asks" in data:
            bids = [
                [_num(p), _num(s)]
                for p, s in data["bids"][:5]
                if _num(p) is not None and _num(s) is not None
            ]
            asks = [
                [_num(p), _num(s)]
                for p, s in data["asks"][:5]
                if _num(p) is not None and _num(s) is not None
            ]
            if bids and asks:
                return RawEvent(
                    source="COINBASE",
                    payload={
                        "instrument": canonical_sym,
                        "event_type": "QUOTE",
                        "exchange_ts": ex_ts,
                        "sequence": seq,
                        "bid": bids[0][0],
                        "ask": asks[0][0],
                        "bid_size": bids[0][1],
                        "ask_size": asks[0][1],
                        "bids": bids,
                        "asks": asks,
                    },
                    receive_timestamp=t_recv,
                    raw_id=f"ws-coinbase-{RUN_ID}-{next(_raw_counter)}",
                    wire=wire,
                )
    except Exception as exc:
        return RawEvent(
            source="COINBASE",
            payload={
                "instrument": canonical_sym,
                "is_malformed": True,
                "error": repr(exc)[:200],
            },
            receive_timestamp=t_recv,
            raw_id=f"ws-coinbase-err-{RUN_ID}-{next(_raw_counter)}",
            wire=wire,
        )
    return None


def parse_kraken_frame(
    data: Any, canonical_sym: str, t_recv: float | None = None, wire: str | bytes = ""
) -> RawEvent | None:
    """Parse Kraken book or ticker WebSocket list-based frame."""
    t_recv = t_recv if t_recv is not None else time.time()
    try:
        if isinstance(data, list) and len(data) >= 2:
            body = data[1]
            if isinstance(body, dict):
                # Book snapshot: "bs" and "as"
                if "bs" in body and "as" in body and body["bs"] and body["as"]:
                    bids = [
                        [_num(row[0]), _num(row[1])]
                        for row in body["bs"][:5]
                        if _num(row[0]) is not None and _num(row[1]) is not None
                    ]
                    asks = [
                        [_num(row[0]), _num(row[1])]
                        for row in body["as"][:5]
                        if _num(row[0]) is not None and _num(row[1]) is not None
                    ]
                    ex_ts = _num(body["bs"][0][2]) if (len(body["bs"][0]) > 2) else None
                    return RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": canonical_sym,
                            "event_type": "QUOTE",
                            "exchange_ts": ex_ts,
                            "sequence": None,
                            "bid": bids[0][0] if bids else None,
                            "ask": asks[0][0] if asks else None,
                            "bid_size": bids[0][1] if bids else None,
                            "ask_size": asks[0][1] if asks else None,
                            "bids": bids,
                            "asks": asks,
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"ws-kraken-{RUN_ID}-{next(_raw_counter)}",
                        wire=wire,
                    )
                # Ticker update: "b" and "a"
                elif "b" in body and "a" in body:
                    bid = _num(body["b"][0])
                    ask = _num(body["a"][0])
                    bid_s = _num(body["b"][2]) if len(body["b"]) > 2 else None
                    ask_s = _num(body["a"][2]) if len(body["a"]) > 2 else None
                    return RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": canonical_sym,
                            "event_type": "QUOTE",
                            "exchange_ts": None,
                            "sequence": None,
                            "bid": bid,
                            "ask": ask,
                            "bid_size": bid_s,
                            "ask_size": ask_s,
                            "bids": [[bid, bid_s]] if bid is not None else [],
                            "asks": [[ask, ask_s]] if ask is not None else [],
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"ws-kraken-{RUN_ID}-{next(_raw_counter)}",
                        wire=wire,
                    )
    except Exception as exc:
        return RawEvent(
            source="KRAKEN",
            payload={
                "instrument": canonical_sym,
                "is_malformed": True,
                "error": repr(exc)[:200],
            },
            receive_timestamp=t_recv,
            raw_id=f"ws-kraken-err-{RUN_ID}-{next(_raw_counter)}",
            wire=wire,
        )
    return None


def parse_okx_frame(
    data: dict, canonical_sym: str, t_recv: float | None = None, wire: str | bytes = ""
) -> RawEvent | None:
    """Parse OKX books5 or tickers WebSocket frame."""
    t_recv = t_recv if t_recv is not None else time.time()
    try:
        items = data.get("data")
        if isinstance(items, list) and len(items) > 0:
            item = items[0]
            ex_ts = (_num(item.get("ts")) / 1000.0) if item.get("ts") else None
            seq = (
                int(item["seqId"])
                if ("seqId" in item and _num(item["seqId"]) is not None)
                else None
            )
            if "bids" in item and "asks" in item and item["bids"] and item["asks"]:
                bids = [
                    [_num(row[0]), _num(row[1])]
                    for row in item["bids"][:5]
                    if _num(row[0]) is not None and _num(row[1]) is not None
                ]
                asks = [
                    [_num(row[0]), _num(row[1])]
                    for row in item["asks"][:5]
                    if _num(row[0]) is not None and _num(row[1]) is not None
                ]
                return RawEvent(
                    source="OKX",
                    payload={
                        "instrument": canonical_sym,
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
                    raw_id=f"ws-okx-{RUN_ID}-{next(_raw_counter)}",
                    wire=wire,
                )
            elif "bidPx" in item and "askPx" in item:
                bid = _num(item.get("bidPx"))
                ask = _num(item.get("askPx"))
                bid_s = _num(item.get("bidSz"))
                ask_s = _num(item.get("askSz"))
                return RawEvent(
                    source="OKX",
                    payload={
                        "instrument": canonical_sym,
                        "event_type": "QUOTE",
                        "exchange_ts": ex_ts,
                        "sequence": seq,
                        "bid": bid,
                        "ask": ask,
                        "bid_size": bid_s,
                        "ask_size": ask_s,
                        "bids": [[bid, bid_s]] if bid is not None else [],
                        "asks": [[ask, ask_s]] if ask is not None else [],
                    },
                    receive_timestamp=t_recv,
                    raw_id=f"ws-okx-{RUN_ID}-{next(_raw_counter)}",
                    wire=wire,
                )
    except Exception as exc:
        return RawEvent(
            source="OKX",
            payload={
                "instrument": canonical_sym,
                "is_malformed": True,
                "error": repr(exc)[:200],
            },
            receive_timestamp=t_recv,
            raw_id=f"ws-okx-err-{RUN_ID}-{next(_raw_counter)}",
            wire=wire,
        )
    return None


def parse_bybit_frame(
    data: dict, canonical_sym: str, t_recv: float | None = None, wire: str | bytes = ""
) -> RawEvent | None:
    """Parse Bybit orderbook.5 or tickers WebSocket frame."""
    t_recv = t_recv if t_recv is not None else time.time()
    try:
        body = data.get("data", {})
        ex_ts = (
            (_num(data.get("ts") or data.get("cts")) / 1000.0)
            if (data.get("ts") or data.get("cts"))
            else None
        )
        seq = (
            int(body.get("seq") or data.get("seq"))
            if (body.get("seq") or data.get("seq"))
            else None
        )
        if "b" in body and "a" in body and body["b"] and body["a"]:
            bids = [
                [_num(row[0]), _num(row[1])]
                for row in body["b"][:5]
                if _num(row[0]) is not None and _num(row[1]) is not None
            ]
            asks = [
                [_num(row[0]), _num(row[1])]
                for row in body["a"][:5]
                if _num(row[0]) is not None and _num(row[1]) is not None
            ]
            return RawEvent(
                source="BYBIT",
                payload={
                    "instrument": canonical_sym,
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
                raw_id=f"ws-bybit-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )
        elif "bid1Price" in body and "ask1Price" in body:
            bid = _num(body.get("bid1Price"))
            ask = _num(body.get("ask1Price"))
            bid_s = _num(body.get("bid1Size"))
            ask_s = _num(body.get("ask1Size"))
            return RawEvent(
                source="BYBIT",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": ex_ts,
                    "sequence": seq,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_s,
                    "ask_size": ask_s,
                    "bids": [[bid, bid_s]] if bid is not None else [],
                    "asks": [[ask, ask_s]] if ask is not None else [],
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-bybit-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )
    except Exception as exc:
        return RawEvent(
            source="BYBIT",
            payload={
                "instrument": canonical_sym,
                "is_malformed": True,
                "error": repr(exc)[:200],
            },
            receive_timestamp=t_recv,
            raw_id=f"ws-bybit-err-{RUN_ID}-{next(_raw_counter)}",
            wire=wire,
        )
    return None


def parse_venue_frame(
    venue: str,
    raw_msg: str | dict,
    canonical_sym: str,
    t_recv: float | None = None,
) -> RawEvent | None:
    """Universal frame parser dispatching to venue-specific unmarshaler."""
    v = venue.upper()
    t_recv = t_recv if t_recv is not None else time.time()
    wire = raw_msg if isinstance(raw_msg, (str, bytes)) else json.dumps(raw_msg)
    if isinstance(raw_msg, str):
        try:
            raw_msg = json.loads(raw_msg)
        except Exception as exc:
            return RawEvent(
                source=v,
                payload={
                    "instrument": canonical_sym,
                    "is_malformed": True,
                    "error": f"JSON parse error: {exc}",
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-{v.lower()}-err-{RUN_ID}-{next(_raw_counter)}",
                wire=wire,
            )

    if v == "BINANCE":
        return parse_binance_frame(raw_msg, canonical_sym, t_recv=t_recv, wire=wire)
    elif v == "COINBASE":
        return parse_coinbase_frame(raw_msg, canonical_sym, t_recv=t_recv, wire=wire)
    elif v == "KRAKEN":
        return parse_kraken_frame(raw_msg, canonical_sym, t_recv=t_recv, wire=wire)
    elif v == "OKX":
        return parse_okx_frame(raw_msg, canonical_sym, t_recv=t_recv, wire=wire)
    elif v == "BYBIT":
        return parse_bybit_frame(raw_msg, canonical_sym, t_recv=t_recv, wire=wire)
    return None


class ConnectorState(str, Enum):
    """Lifecycle state of an upstream market data exchange connector."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"


@dataclass(slots=True)
class ProviderTelemetry:
    """Standardized institutional telemetry contract for market data feed health."""

    provider_id: str
    state: ConnectorState
    active_subscriptions: list[str]
    last_received_ts: float | None
    last_processed_ts: float | None
    feed_freshness_ms: float | None
    reconnect_count: int
    error_count: int
    dropped_count: int
    sequence_gaps_count: int
    last_sequence_num: int | None
    wire_latency_us: float | None
    data_mode: str = "LIVE"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = (
            self.state.value
            if isinstance(self.state, ConnectorState)
            else str(self.state)
        )
        return d


class WebSocketFeedManager:
    """Multi-Connection WebSocket Feed Manager.

    Maintains persistent full-duplex connections to crypto exchange endpoints concurrently,
    bridging events across threads into a bounded thread-safe queue.
    """

    def __init__(
        self,
        symbols: list[str],
        venues: list[str] | None = None,
        max_queue_size: int = 10000,
        on_drop: Callable[[RawEvent], None] | None = None,
    ):
        self.symbols = symbols
        self.venues = (
            [v.upper() for v in venues]
            if venues
            else ["BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"]
        )
        self.max_queue_size = max_queue_size
        self._queue: queue.Queue[RawEvent] = queue.Queue(maxsize=max_queue_size)
        self._on_drop = on_drop
        self._tombstones: collections.deque[RawEvent] = collections.deque(maxlen=1000)
        self._tombstone_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stats: dict[str, dict] = {
            v: {"connected": False, "frames": 0, "reconnects": 0, "errors": 0}
            for v in self.venues
        }
        self._provider_meta: dict[str, dict] = {
            v: {
                "last_received_ts": None,
                "last_processed_ts": None,
                "sequence_gaps_count": 0,
                "last_sequence_num": None,
                "last_wire_latency_us": None,
                "error_count": 0,
            }
            for v in self.venues
        }
        self._drop_count: int = 0  # Total frames dropped due to backpressure
        self._drop_counts: dict[str, int] = {v: 0 for v in self.venues}
        self._ssl_ctx = ssl.create_default_context()

    def _create_drop_tombstone(
        self, venue: str, symbol: str, evicted: RawEvent | None = None
    ) -> RawEvent:
        """Create an explicit tombstone event representing dropped data under queue backpressure."""
        return RawEvent(
            source=venue,
            payload={
                "instrument": symbol,
                "event_type": "TOMBSTONE_DROPPED",
                "is_dropped": True,
                "drop_reason": "BACKPRESSURE_DROP",
                "drop_count": self._drop_count,
                "venue": venue,
                "evicted_raw_id": evicted.raw_id if evicted else None,
            },
            receive_timestamp=time.time(),
            raw_id=f"ws-tombstone-{venue.lower()}-{self._drop_count}",
        )

    def get_dropped_tombstones(self) -> list[RawEvent]:
        """Return recently recorded backpressure drop tombstone events."""
        with self._tombstone_lock:
            return list(self._tombstones)

    def start(self) -> None:
        """Start the background asynchronous WebSocket workers."""
        if not HAS_WEBSOCKETS:
            logger.warning(
                "[ws_feed] 'websockets' library not installed. WebSocket streaming disabled."
            )
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_async_loop, daemon=True, name="mdrap-ws-manager"
        )
        self._thread.start()

    def stop(self) -> None:
        """Gracefully stop all WebSocket connections."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def stats(self) -> dict[str, dict]:
        result = dict(self._stats)
        result["_drops"] = {
            "total": self._drop_count,
            "per_venue": dict(self._drop_counts),
        }
        return result

    def get_provider_telemetry(self) -> dict[str, ProviderTelemetry]:
        """Expose standardized telemetry contracts across all active exchange connectors."""
        now = time.time()
        telemetry: dict[str, ProviderTelemetry] = {}
        for v in self.venues:
            meta = self._provider_meta.get(v, {})
            stats = self._stats.get(v, {})
            is_conn = stats.get("connected", False)
            last_rx = meta.get("last_received_ts")

            freshness_ms: float | None = None
            state = ConnectorState.DISCONNECTED
            if is_conn:
                if last_rx is not None:
                    freshness_ms = max(0.0, (now - last_rx) * 1000.0)
                    if freshness_ms > 10_000.0:  # >10s without frame on active socket
                        state = ConnectorState.STALE
                    elif (
                        meta.get("sequence_gaps_count", 0) > 0
                        or meta.get("error_count", 0) > 0
                    ):
                        state = ConnectorState.DEGRADED
                    else:
                        state = ConnectorState.CONNECTED
                else:
                    state = ConnectorState.CONNECTING
            elif stats.get("reconnects", 0) > 0:
                state = ConnectorState.RECONNECTING

            lat_us = meta.get("last_wire_latency_us")
            telemetry[v] = ProviderTelemetry(
                provider_id=v,
                state=state,
                active_subscriptions=list(self.symbols),
                last_received_ts=last_rx,
                last_processed_ts=meta.get("last_processed_ts"),
                feed_freshness_ms=round(freshness_ms, 2)
                if freshness_ms is not None
                else None,
                reconnect_count=stats.get("reconnects", 0),
                error_count=stats.get("errors", 0),
                dropped_count=self._drop_counts.get(v, 0),
                sequence_gaps_count=meta.get("sequence_gaps_count", 0),
                last_sequence_num=meta.get("last_sequence_num"),
                wire_latency_us=round(lat_us, 2) if lat_us is not None else None,
                data_mode="LIVE",
            )
        return telemetry

    def _run_async_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        tasks = []
        for v in self.venues:
            for s in self.symbols:
                tasks.append(self._venue_worker(v, s))

        try:
            self._loop.run_until_complete(
                asyncio.gather(*tasks, return_exceptions=True)
            )
        except Exception as exc:
            logger.debug("[ws_feed] Worker loop stopped: %s", exc)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception as exc:
                logger.debug("[ws_feed] Task cancellation cleanup: %s", exc)
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception as exc:
                logger.debug("[ws_feed] Shutdown asyncgens note: %s", exc)
            self._loop.close()

    async def _venue_worker(self, venue: str, symbol: str) -> None:
        """Persistent connection worker for a specific venue and symbol."""
        mapping = resolve_venue_symbols(symbol)
        v_sym = mapping.get(venue.lower(), "")
        if not v_sym:
            return

        canon = mapping["canonical"]
        backoff = 1.0

        while not self._stop_event.is_set():
            ws_url = self._get_url(venue, v_sym)
            if not ws_url:
                return

            try:
                self._stats[venue]["reconnects"] += 1
                async with websockets.connect(
                    ws_url,
                    ssl=self._ssl_ctx,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self._stats[venue]["connected"] = True
                    backoff = 1.0

                    # Send subscribe message if required by protocol
                    sub_msg = self._get_subscribe_msg(venue, v_sym)
                    if sub_msg:
                        await ws.send(json.dumps(sub_msg))

                    while not self._stop_event.is_set():
                        try:
                            frame = await asyncio.wait_for(ws.recv(), timeout=15.0)
                            raw = parse_venue_frame(venue, frame, canon)
                            if raw:
                                self._stats[venue]["frames"] += 1
                                meta = self._provider_meta[venue]
                                now_t = time.time()
                                meta["last_received_ts"] = now_t
                                meta["last_processed_ts"] = now_t

                                # Track sequence numbers and sequence gaps
                                seq = raw.payload.get("sequence")
                                if seq is not None and isinstance(seq, int):
                                    prev_seq = meta.get("last_sequence_num")
                                    if prev_seq is not None and seq > prev_seq + 1:
                                        meta["sequence_gaps_count"] += (
                                            seq - prev_seq - 1
                                        )
                                    meta["last_sequence_num"] = seq

                                # Track wire network latency
                                ex_ts = raw.payload.get("exchange_ts")
                                if (
                                    ex_ts is not None
                                    and isinstance(ex_ts, (int, float))
                                    and ex_ts > 0
                                ):
                                    lat = (raw.receive_timestamp - ex_ts) * 1e6
                                    if 0 <= lat <= 30_000_000:
                                        meta["last_wire_latency_us"] = lat

                                try:
                                    self._queue.put_nowait(raw)
                                except queue.Full:
                                    # Evict oldest frame to keep stream real-time
                                    evicted: RawEvent | None = None
                                    try:
                                        evicted = self._queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                    self._queue.put_nowait(raw)
                                    self._drop_count += 1
                                    self._drop_counts[venue] = (
                                        self._drop_counts.get(venue, 0) + 1
                                    )
                                    tombstone = self._create_drop_tombstone(
                                        venue, canon, evicted
                                    )
                                    with self._tombstone_lock:
                                        self._tombstones.append(tombstone)
                                    if self._on_drop is not None:
                                        try:
                                            self._on_drop(tombstone)
                                        except Exception as cb_exc:
                                            logger.debug(
                                                "[ws_feed] on_drop callback error: %s",
                                                cb_exc,
                                            )
                                    if self._drop_count % 100 == 1:
                                        logger.warning(
                                            "[ws_feed] Backpressure drop #%d on %s (total: %d)",
                                            self._drop_counts[venue],
                                            venue,
                                            self._drop_count,
                                        )
                        except asyncio.TimeoutError:
                            # Send ping keepalive
                            await ws.ping()
            except Exception as exc:
                self._stats[venue]["errors"] += 1
                self._provider_meta[venue]["error_count"] += 1
                logger.warning(
                    "[ws_feed] %s connection error: %s", venue, repr(exc)[:200]
                )
                self._stats[venue]["connected"] = False
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2.0, 30.0)

    def _get_url(self, venue: str, venue_sym: str) -> str:
        if venue == "BINANCE":
            stream = f"{venue_sym.lower()}@depth5@100ms"
            return WS_ENDPOINTS["BINANCE"].format(stream=stream)
        return WS_ENDPOINTS.get(venue, "")

    def _get_subscribe_msg(self, venue: str, venue_sym: str) -> dict | None:
        if venue == "COINBASE":
            return {
                "type": "subscribe",
                "product_ids": [venue_sym],
                "channels": ["ticker"],
            }
        elif venue == "KRAKEN":
            k_sym = (
                venue_sym
                if "/" in venue_sym
                else (
                    f"{venue_sym[:-3]}/{venue_sym[-3:]}"
                    if len(venue_sym) >= 6
                    else venue_sym
                )
            )
            return {
                "event": "subscribe",
                "pair": [k_sym],
                "subscription": {"name": "book", "depth": 10},
            }
        elif venue == "OKX":
            return {
                "op": "subscribe",
                "args": [{"channel": "books5", "instId": venue_sym}],
            }
        elif venue == "BYBIT":
            return {"op": "subscribe", "args": [f"orderbook.1.{venue_sym}"]}
        return None

    def stream_events(
        self,
        limit: int | None = None,
        timeout_s: float = 2.0,
    ) -> Generator[RawEvent, None, None]:
        """
        Synchronously yield RawEvents from the WebSocket background worker.
        Matching MDRAP pipeline processing interface.
        """
        count = 0
        while not self._stop_event.is_set():
            try:
                ev = self._queue.get(timeout=timeout_s)
                yield ev
                count += 1
                if limit and count >= limit:
                    return
            except queue.Empty:
                if not self.is_running():
                    return


# ---------------------------------------------------------------------------
# Feed Parsing & Sequence Tracking Upgrades (§25)
# ---------------------------------------------------------------------------


@dataclass
class ParsedBookFrame:
    venue: str
    instrument: str
    bid_price: float | None = None
    ask_price: float | None = None
    bid_quantity: float | None = None
    ask_quantity: float | None = None
    sequence_number: int | None = None
    timestamp_ns: int | None = None
    is_snapshot: bool = False


def parse_binance_depth_payload(raw: str | bytes) -> ParsedBookFrame | None:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        bids = data.get("b", data.get("bids", []))
        asks = data.get("a", data.get("asks", []))
        if not bids or not asks:
            return None
        b_p, b_q = float(bids[0][0]), float(bids[0][1])
        a_p, a_q = float(asks[0][0]), float(asks[0][1])
        seq = int(data.get("u", data.get("lastUpdateId", 0)))
        sym = data.get("s", "BTC-USDT")
        ts_ms = data.get("E", int(time.time() * 1000))
        return ParsedBookFrame(
            venue="BINANCE",
            instrument=sym,
            bid_price=b_p,
            ask_price=a_p,
            bid_quantity=b_q,
            ask_quantity=a_q,
            sequence_number=seq,
            timestamp_ns=int(ts_ms * 1_000_000),
        )
    except Exception:
        return None


def parse_coinbase_ticker_payload(raw: str | bytes) -> ParsedBookFrame | None:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("type") != "ticker":
            return None
        b_p = float(data.get("best_bid"))
        a_p = float(data.get("best_ask"))
        b_q = float(data.get("best_bid_size", 0.0))
        a_q = float(data.get("best_ask_size", 0.0))
        seq = int(data.get("sequence", 0))
        sym = data.get("product_id", "BTC-USD")
        return ParsedBookFrame(
            venue="COINBASE",
            instrument=sym,
            bid_price=b_p,
            ask_price=a_p,
            bid_quantity=b_q,
            ask_quantity=a_q,
            sequence_number=seq,
            timestamp_ns=int(time.time() * 1_000_000_000),
        )
    except Exception:
        return None


def parse_kraken_ticker_payload(raw: str | bytes) -> ParsedBookFrame | None:
    try:
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) < 4:
            return None
        payload = data[1]
        pair = str(data[3])
        b_info = payload.get("b", [])
        a_info = payload.get("a", [])
        if not b_info or not a_info:
            return None
        b_p = float(b_info[0])
        b_q = float(b_info[2]) if len(b_info) > 2 else float(b_info[1])
        a_p = float(a_info[0])
        a_q = float(a_info[2]) if len(a_info) > 2 else float(a_info[1])
        return ParsedBookFrame(
            venue="KRAKEN",
            instrument=pair,
            bid_price=b_p,
            ask_price=a_p,
            bid_quantity=b_q,
            ask_quantity=a_q,
            sequence_number=None,
            timestamp_ns=int(time.time() * 1_000_000_000),
        )
    except Exception:
        return None


def parse_okx_bbo_payload(raw: str | bytes) -> ParsedBookFrame | None:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        d_list = data.get("data", [])
        if not d_list:
            return None
        item = d_list[0]
        bids = item.get("bids", [])
        asks = item.get("asks", [])
        if not bids or not asks:
            return None
        b_p = float(bids[0][0])
        b_q = float(bids[0][1])
        a_p = float(asks[0][0])
        a_q = float(asks[0][1])
        seq = int(item.get("seqId", 0))
        inst = data.get("arg", {}).get("instId", "BTC-USDT")
        ts_ms = int(item.get("ts", int(time.time() * 1000)))
        return ParsedBookFrame(
            venue="OKX",
            instrument=inst,
            bid_price=b_p,
            ask_price=a_p,
            bid_quantity=b_q,
            ask_quantity=a_q,
            sequence_number=seq,
            timestamp_ns=int(ts_ms * 1_000_000),
        )
    except Exception:
        return None


def parse_bybit_bbo_payload(raw: str | bytes) -> ParsedBookFrame | None:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        inner = data.get("data", {})
        bids = inner.get("b", [])
        asks = inner.get("a", [])
        if not bids or not asks:
            return None
        b_p = float(bids[0][0])
        b_q = float(bids[0][1])
        a_p = float(asks[0][0])
        a_q = float(asks[0][1])
        seq = int(inner.get("seq", inner.get("u", 0)))
        sym = inner.get("s", "BTCUSDT")
        ts_ms = int(data.get("ts", int(time.time() * 1000)))
        return ParsedBookFrame(
            venue="BYBIT",
            instrument=sym,
            bid_price=b_p,
            ask_price=a_p,
            bid_quantity=b_q,
            ask_quantity=a_q,
            sequence_number=seq,
            timestamp_ns=int(ts_ms * 1_000_000),
        )
    except Exception:
        return None


class LiveCryptoFeed:
    """High-performance multi-venue sequence and gap tracking feed wrapper."""

    def __init__(self):
        self.last_seq: dict[str, int] = {}
        self.gap_counts: dict[str, int] = collections.defaultdict(int)
        self.missing_events_count: dict[str, int] = collections.defaultdict(int)

    def check_sequence_gap(self, venue: str, seq: int) -> bool:
        prev = self.last_seq.get(venue)
        self.last_seq[venue] = seq
        if prev is None:
            return False
        if seq > prev + 1:
            missing = seq - prev - 1
            self.gap_counts[venue] += 1
            self.missing_events_count[venue] += missing
            return True
        return False
