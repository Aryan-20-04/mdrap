"""
Multi-Venue Persistent WebSocket Feed Engine for MDRAP.

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
import itertools
import json
import logging
import queue
import ssl
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from live import resolve_venue_symbols
from models import RawEvent

logger = logging.getLogger("mdrap.ws_feed")

_seq_counter = itertools.count(1)
_raw_counter = itertools.count(1)

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


def parse_binance_frame(data: dict, canonical_sym: str) -> Optional[RawEvent]:
    """Parse Binance @depth5 or @bookTicker WebSocket JSON frame."""
    t_recv = time.time()
    try:
        # Check depth format (e.g. @depth5)
        if "bids" in data and "asks" in data and data["bids"] and data["asks"]:
            bids = [[float(p), float(s)] for p, s in data["bids"]]
            asks = [[float(p), float(s)] for p, s in data["asks"]]
            best_bid, best_bid_size = bids[0]
            best_ask, best_ask_size = asks[0]
            return RawEvent(
                source="BINANCE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": t_recv - 0.002,
                    "sequence": next(_seq_counter),
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": best_bid_size,
                    "ask_size": best_ask_size,
                    "bids": bids,
                    "asks": asks,
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-binance-{next(_raw_counter)}",
            )

        # Check bookTicker format
        if "b" in data and "a" in data:
            best_bid = float(data["b"])
            best_bid_size = float(data.get("B", 1.0))
            best_ask = float(data["a"])
            best_ask_size = float(data.get("A", 1.0))
            return RawEvent(
                source="BINANCE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": t_recv - 0.002,
                    "sequence": next(_seq_counter),
                    "bid": best_bid,
                    "ask": best_ask,
                    "bid_size": best_bid_size,
                    "ask_size": best_ask_size,
                    "bids": [[best_bid, best_bid_size]],
                    "asks": [[best_ask, best_ask_size]],
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-binance-{next(_raw_counter)}",
            )
    except Exception as exc:
        return RawEvent(
            source="BINANCE",
            payload={"instrument": canonical_sym, "is_malformed": True, "error": str(exc)},
            receive_timestamp=t_recv,
            raw_id=f"ws-binance-err-{next(_raw_counter)}",
        )
    return None


def parse_coinbase_frame(data: dict, canonical_sym: str) -> Optional[RawEvent]:
    """Parse Coinbase ticker or snapshot/l2update WebSocket frame."""
    t_recv = time.time()
    try:
        msg_type = data.get("type")
        if msg_type == "ticker":
            bid = float(data["best_bid"])
            ask = float(data["best_ask"])
            bid_s = float(data.get("best_bid_size", 1.0))
            ask_s = float(data.get("best_ask_size", 1.0))
            return RawEvent(
                source="COINBASE",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": t_recv - 0.003,
                    "sequence": next(_seq_counter),
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_s,
                    "ask_size": ask_s,
                    "bids": [[bid, bid_s]],
                    "asks": [[ask, ask_s]],
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-coinbase-{next(_raw_counter)}",
            )
        elif msg_type == "snapshot" and "bids" in data and "asks" in data:
            bids = [[float(p), float(s)] for p, s in data["bids"][:5]]
            asks = [[float(p), float(s)] for p, s in data["asks"][:5]]
            if bids and asks:
                return RawEvent(
                    source="COINBASE",
                    payload={
                        "instrument": canonical_sym,
                        "event_type": "QUOTE",
                        "exchange_ts": t_recv - 0.003,
                        "sequence": next(_seq_counter),
                        "bid": bids[0][0],
                        "ask": asks[0][0],
                        "bid_size": bids[0][1],
                        "ask_size": asks[0][1],
                        "bids": bids,
                        "asks": asks,
                    },
                    receive_timestamp=t_recv,
                    raw_id=f"ws-coinbase-{next(_raw_counter)}",
                )
    except Exception as exc:
        return RawEvent(
            source="COINBASE",
            payload={"instrument": canonical_sym, "is_malformed": True, "error": str(exc)},
            receive_timestamp=t_recv,
            raw_id=f"ws-coinbase-err-{next(_raw_counter)}",
        )
    return None


def parse_kraken_frame(data: Any, canonical_sym: str) -> Optional[RawEvent]:
    """Parse Kraken book or ticker WebSocket list-based frame."""
    t_recv = time.time()
    try:
        if isinstance(data, list) and len(data) >= 2:
            body = data[1]
            if isinstance(body, dict):
                # Book snapshot: "bs" and "as"
                if "bs" in body and "as" in body and body["bs"] and body["as"]:
                    bids = [[float(row[0]), float(row[1])] for row in body["bs"][:5]]
                    asks = [[float(row[0]), float(row[1])] for row in body["as"][:5]]
                    return RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": canonical_sym,
                            "event_type": "QUOTE",
                            "exchange_ts": t_recv - 0.004,
                            "sequence": next(_seq_counter),
                            "bid": bids[0][0],
                            "ask": asks[0][0],
                            "bid_size": bids[0][1],
                            "ask_size": asks[0][1],
                            "bids": bids,
                            "asks": asks,
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"ws-kraken-{next(_raw_counter)}",
                    )
                # Ticker update: "b" and "a"
                elif "b" in body and "a" in body:
                    bid = float(body["b"][0])
                    ask = float(body["a"][0])
                    bid_s = float(body["b"][2])
                    ask_s = float(body["a"][2])
                    return RawEvent(
                        source="KRAKEN",
                        payload={
                            "instrument": canonical_sym,
                            "event_type": "QUOTE",
                            "exchange_ts": t_recv - 0.004,
                            "sequence": next(_seq_counter),
                            "bid": bid,
                            "ask": ask,
                            "bid_size": bid_s,
                            "ask_size": ask_s,
                            "bids": [[bid, bid_s]],
                            "asks": [[ask, ask_s]],
                        },
                        receive_timestamp=t_recv,
                        raw_id=f"ws-kraken-{next(_raw_counter)}",
                    )
    except Exception as exc:
        return RawEvent(
            source="KRAKEN",
            payload={"instrument": canonical_sym, "is_malformed": True, "error": str(exc)},
            receive_timestamp=t_recv,
            raw_id=f"ws-kraken-err-{next(_raw_counter)}",
        )
    return None


def parse_okx_frame(data: dict, canonical_sym: str) -> Optional[RawEvent]:
    """Parse OKX books5 or tickers WebSocket frame."""
    t_recv = time.time()
    try:
        items = data.get("data")
        if isinstance(items, list) and len(items) > 0:
            item = items[0]
            if "bids" in item and "asks" in item and item["bids"] and item["asks"]:
                bids = [[float(row[0]), float(row[1])] for row in item["bids"][:5]]
                asks = [[float(row[0]), float(row[1])] for row in item["asks"][:5]]
                return RawEvent(
                    source="OKX",
                    payload={
                        "instrument": canonical_sym,
                        "event_type": "QUOTE",
                        "exchange_ts": t_recv - 0.003,
                        "sequence": next(_seq_counter),
                        "bid": bids[0][0],
                        "ask": asks[0][0],
                        "bid_size": bids[0][1],
                        "ask_size": asks[0][1],
                        "bids": bids,
                        "asks": asks,
                    },
                    receive_timestamp=t_recv,
                    raw_id=f"ws-okx-{next(_raw_counter)}",
                )
            elif "bidPx" in item and "askPx" in item:
                bid = float(item["bidPx"])
                ask = float(item["askPx"])
                bid_s = float(item.get("bidSz", 1.0))
                ask_s = float(item.get("askSz", 1.0))
                return RawEvent(
                    source="OKX",
                    payload={
                        "instrument": canonical_sym,
                        "event_type": "QUOTE",
                        "exchange_ts": t_recv - 0.003,
                        "sequence": next(_seq_counter),
                        "bid": bid,
                        "ask": ask,
                        "bid_size": bid_s,
                        "ask_size": ask_s,
                        "bids": [[bid, bid_s]],
                        "asks": [[ask, ask_s]],
                    },
                    receive_timestamp=t_recv,
                    raw_id=f"ws-okx-{next(_raw_counter)}",
                )
    except Exception as exc:
        return RawEvent(
            source="OKX",
            payload={"instrument": canonical_sym, "is_malformed": True, "error": str(exc)},
            receive_timestamp=t_recv,
            raw_id=f"ws-okx-err-{next(_raw_counter)}",
        )
    return None


def parse_bybit_frame(data: dict, canonical_sym: str) -> Optional[RawEvent]:
    """Parse Bybit orderbook.5 or tickers WebSocket frame."""
    t_recv = time.time()
    try:
        body = data.get("data", {})
        if "b" in body and "a" in body and body["b"] and body["a"]:
            bids = [[float(row[0]), float(row[1])] for row in body["b"][:5]]
            asks = [[float(row[0]), float(row[1])] for row in body["a"][:5]]
            return RawEvent(
                source="BYBIT",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": t_recv - 0.003,
                    "sequence": next(_seq_counter),
                    "bid": bids[0][0],
                    "ask": asks[0][0],
                    "bid_size": bids[0][1],
                    "ask_size": asks[0][1],
                    "bids": bids,
                    "asks": asks,
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-bybit-{next(_raw_counter)}",
            )
        elif "bid1Price" in body and "ask1Price" in body:
            bid = float(body["bid1Price"])
            ask = float(body["ask1Price"])
            bid_s = float(body.get("bid1Size", 1.0))
            ask_s = float(body.get("ask1Size", 1.0))
            return RawEvent(
                source="BYBIT",
                payload={
                    "instrument": canonical_sym,
                    "event_type": "QUOTE",
                    "exchange_ts": t_recv - 0.003,
                    "sequence": next(_seq_counter),
                    "bid": bid,
                    "ask": ask,
                    "bid_size": bid_s,
                    "ask_size": ask_s,
                    "bids": [[bid, bid_s]],
                    "asks": [[ask, ask_s]],
                },
                receive_timestamp=t_recv,
                raw_id=f"ws-bybit-{next(_raw_counter)}",
            )
    except Exception as exc:
        return RawEvent(
            source="BYBIT",
            payload={"instrument": canonical_sym, "is_malformed": True, "error": str(exc)},
            receive_timestamp=t_recv,
            raw_id=f"ws-bybit-err-{next(_raw_counter)}",
        )
    return None


def parse_venue_frame(venue: str, raw_msg: str | dict, canonical_sym: str) -> Optional[RawEvent]:
    """Universal frame parser dispatching to venue-specific unmarshaler."""
    v = venue.upper()
    if isinstance(raw_msg, str):
        try:
            raw_msg = json.loads(raw_msg)
        except Exception as exc:
            return RawEvent(
                source=v,
                payload={"instrument": canonical_sym, "is_malformed": True, "error": f"JSON parse error: {exc}"},
                receive_timestamp=time.time(),
                raw_id=f"ws-{v.lower()}-err-{next(_raw_counter)}",
            )

    if v == "BINANCE":
        return parse_binance_frame(raw_msg, canonical_sym)
    elif v == "COINBASE":
        return parse_coinbase_frame(raw_msg, canonical_sym)
    elif v == "KRAKEN":
        return parse_kraken_frame(raw_msg, canonical_sym)
    elif v == "OKX":
        return parse_okx_frame(raw_msg, canonical_sym)
    elif v == "BYBIT":
        return parse_bybit_frame(raw_msg, canonical_sym)
    return None


class WebSocketFeedManager:
    """
    Multi-Connection WebSocket Feed Manager.
    Maintains persistent connections to crypto exchanges concurrently.
    """

    def __init__(
        self,
        symbols: List[str],
        venues: Optional[List[str]] = None,
        max_queue_size: int = 10000,
    ):
        self.symbols = symbols
        self.venues = [v.upper() for v in venues] if venues else ["BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"]
        self.max_queue_size = max_queue_size
        self._queue: queue.Queue[RawEvent] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stats: Dict[str, dict] = {
            v: {"connected": False, "frames": 0, "reconnects": 0, "errors": 0}
            for v in self.venues
        }
        self._ssl_ctx = ssl.create_default_context()

    def start(self) -> None:
        """Start the background asynchronous WebSocket workers."""
        if not HAS_WEBSOCKETS:
            logger.warning("[ws_feed] 'websockets' library not installed. WebSocket streaming disabled.")
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True, name="mdrap-ws-manager")
        self._thread.start()

    def stop(self) -> None:
        """Gracefully stop all WebSocket connections."""
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def stats(self) -> Dict[str, dict]:
        return dict(self._stats)

    def _run_async_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        tasks = []
        for v in self.venues:
            for s in self.symbols:
                tasks.append(self._venue_worker(v, s))

        try:
            self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        except Exception:
            pass
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
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
            except Exception as e:
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

    def _get_subscribe_msg(self, venue: str, venue_sym: str) -> Optional[dict]:
        if venue == "COINBASE":
            return {"type": "subscribe", "product_ids": [venue_sym], "channels": ["ticker", "level2_batch"]}
        elif venue == "KRAKEN":
            return {"event": "subscribe", "pair": [venue_sym], "subscription": {"name": "book", "depth": 10}}
        elif venue == "OKX":
            return {"op": "subscribe", "args": [{"channel": "books5", "instId": venue_sym}]}
        elif venue == "BYBIT":
            return {"op": "subscribe", "args": [f"orderbook.5.{venue_sym}"]}
        return None

    def stream_events(
        self,
        limit: Optional[int] = None,
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
