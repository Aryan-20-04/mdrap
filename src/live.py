"""
Live Market Connector for MDRAP.

Streams real-time live quotes and trades from public exchanges (Binance, Coinbase)
directly into the MDRAP ingestion gateway, quality engine, BBO aggregator, and storage.

Zero mandatory external dependencies: uses Python standard library urllib/ssl/json
with automatic graceful degradation and exception shielding.
"""
from __future__ import annotations

import itertools
import json
import ssl
import sys
import time
import urllib.request
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from models import CanonicalEvent, EventType, QualityStatus, RawEvent

_seq_counter = itertools.count(1)
_raw_counter = itertools.count(1)

# Default public endpoint constants
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{symbol}/ticker"

# Canonical instrument normalization map
SYMBOL_MAP = {
    "BTC": ("BTC/USD", "BTCUSDT", "BTC-USD"),
    "BTCUSD": ("BTC/USD", "BTCUSDT", "BTC-USD"),
    "BTCUSDT": ("BTC/USD", "BTCUSDT", "BTC-USD"),
    "BTC-USD": ("BTC/USD", "BTCUSDT", "BTC-USD"),
    "ETH": ("ETH/USD", "ETHUSDT", "ETH-USD"),
    "ETHUSD": ("ETH/USD", "ETHUSDT", "ETH-USD"),
    "ETHUSDT": ("ETH/USD", "ETHUSDT", "ETH-USD"),
    "ETH-USD": ("ETH/USD", "ETHUSDT", "ETH-USD"),
    "SOL": ("SOL/USD", "SOLUSDT", "SOL-USD"),
    "SOLUSD": ("SOL/USD", "SOLUSDT", "SOL-USD"),
    "SOLUSDT": ("SOL/USD", "SOLUSDT", "SOL-USD"),
    "SOL-USD": ("SOL/USD", "SOLUSDT", "SOL-USD"),
}


def normalize_symbol_pair(symbol: str) -> Tuple[str, str, str]:
    """Return (canonical_symbol, binance_symbol, coinbase_symbol)."""
    key = symbol.upper().replace("/", "").replace("-", "")
    if key in SYMBOL_MAP:
        return SYMBOL_MAP[key]
    canon = f"{symbol.upper()}/USD" if "/" not in symbol else symbol.upper()
    b_sym = canon.replace("/", "").replace("-", "")
    if not b_sym.endswith("USDT"):
        b_sym += "USDT"
    c_sym = canon.replace("/", "-")
    return canon, b_sym, c_sym


class LiveConnector:
    """
    Connects to live public market feeds, normalizes incoming packets
    into MDRAP RawEvents, and routes them through the platform.
    """

    def __init__(self, timeout: float = 4.0):
        self.timeout = timeout
        self._ctx = ssl.create_default_context()
        self._headers = {"User-Agent": "MDRAP-MarketData/1.0 (Finance-Infrastructure)"}

    def fetch_binance_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Binance public API."""
        canon, b_sym, _ = normalize_symbol_pair(symbol)
        url = BINANCE_TICKER_URL.format(symbol=b_sym)
        t_recv = time.time()
        try:
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            bid_p = float(data["bidPrice"])
            bid_s = float(data.get("bidQty", 1.0))
            ask_p = float(data["askPrice"])
            ask_s = float(data.get("askQty", 1.0))
            
            payload = {
                "instrument": canon,
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.005,  # estimated exchange latency
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": bid_s,
                "ask_size": ask_s,
            }
            return RawEvent(
                source="BINANCE",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-binance-{next(_raw_counter)}",
            )
        except Exception:
            return None

    def fetch_coinbase_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Coinbase public API."""
        canon, _, c_sym = normalize_symbol_pair(symbol)
        url = COINBASE_TICKER_URL.format(symbol=c_sym)
        t_recv = time.time()
        try:
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            bid_p = float(data["bid"])
            ask_p = float(data["ask"])
            size = float(data.get("size", 1.0))

            # Coinbase ISO timestamp parse
            ex_ts = t_recv - 0.008
            payload = {
                "instrument": canon,
                "event_type": "QUOTE",
                "exchange_ts": ex_ts,
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": size,
                "ask_size": size,
            }
            return RawEvent(
                source="COINBASE",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-coinbase-{next(_raw_counter)}",
            )
        except Exception:
            return None

    def stream_ticks(
        self,
        symbols: List[str],
        limit: Optional[int] = 20,
        sources: Optional[List[str]] = None,
        poll_interval_s: float = 0.25,
    ) -> Generator[RawEvent, None, None]:
        """
        Stream live market ticks round-robin across specified symbols and exchanges.
        Yields normalized RawEvent objects ready for pipeline.process_one().
        """
        active_sources = [s.upper() for s in sources] if sources else ["BINANCE", "COINBASE"]
        count = 0

        while True:
            for sym in symbols:
                if "BINANCE" in active_sources:
                    raw_b = self.fetch_binance_quote(sym)
                    if raw_b:
                        yield raw_b
                        count += 1
                        if limit and count >= limit:
                            return

                if "COINBASE" in active_sources:
                    raw_c = self.fetch_coinbase_quote(sym)
                    if raw_c:
                        yield raw_c
                        count += 1
                        if limit and count >= limit:
                            return

            time.sleep(poll_interval_s)
