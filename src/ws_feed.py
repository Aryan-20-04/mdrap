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
from collections.abc import Generator
from datetime import datetime, timezone
import itertools
import json
import logging
import math
import queue
import ssl
import threading
import time
from typing import Any
import uuid

from live import resolve_venue_symbols
from models import RawEvent

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
                    ex_ts = (
                        _num(body["bs"][0][2])
                        if (len(body["bs"][0]) > 2)
                        else None
                    )
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
    ):
        self.symbols = symbols
        self.venues = (
            [v.upper() for v in venues]
            if venues
            else ["BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"]
        )
        self.max_queue_size = max_queue_size
        self._queue: queue.Queue[RawEvent] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stats: dict[str, dict] = {
            v: {"connected": False, "frames": 0, "reconnects": 0, "errors": 0}
            for v in self.venues
        }
        self._ssl_ctx = ssl.create_default_context()

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
        return dict(self._stats)

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
        except Exception:
            pass
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
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
                                try:
                                    self._queue.put_nowait(raw)
                                except queue.Full:
                                    # Evict oldest frame to keep stream real-time
                                    try:
                                        self._queue.get_nowait()
                                    except queue.Empty:
                                        pass
                                    self._queue.put_nowait(raw)
                        except asyncio.TimeoutError:
                            # Send ping keepalive
                            await ws.ping()
            except Exception:
                self._stats[venue]["errors"] += 1
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
                "channels": ["ticker", "level2_batch"],
            }
        elif venue == "KRAKEN":
            return {
                "event": "subscribe",
                "pair": [venue_sym],
                "subscription": {"name": "book", "depth": 10},
            }
        elif venue == "OKX":
            return {
                "op": "subscribe",
                "args": [{"channel": "books5", "instId": venue_sym}],
            }
        elif venue == "BYBIT":
            return {"op": "subscribe", "args": [f"orderbook.5.{venue_sym}"]}
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
