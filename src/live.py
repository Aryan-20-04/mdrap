"""
Live Market Connector for MDRAP.

Streams real-time live quotes and trades from global financial venues:
- Cryptocurrency Venues: Binance, Coinbase, Kraken, OKX, Bybit
- Equity & Commodity Feeds: Yahoo Finance (AAPL, MSFT, NVDA, TSLA, SPY, QQQ, Gold)

Directly routes into the MDRAP ingestion gateway, quality engine, BBO aggregator, and storage.
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

# Public REST endpoint constants
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{symbol}/ticker"
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair={symbol}"
OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker?instId={symbol}"
BYBIT_TICKER_URL = "https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d"

# Known Equity & Commodity symbols
KNOWN_EQUITIES = {
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "NVDA": "NVDA",
    "TSLA": "TSLA",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "META": "META",
    "JPM": "JPM",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GOLD": "GC=F",
}

# Crypto venue symbol mappings: canonical -> (binance, coinbase, kraken, okx, bybit)
CRYPTO_VENUE_MAP: Dict[str, Tuple[str, str, str, str, str]] = {
    "BTC/USD": ("BTCUSDT", "BTC-USD", "XBTUSD", "BTC-USDT", "BTCUSDT"),
    "ETH/USD": ("ETHUSDT", "ETH-USD", "ETHUSD", "ETH-USDT", "ETHUSDT"),
    "SOL/USD": ("SOLUSDT", "SOL-USD", "SOLUSD", "SOL-USDT", "SOLUSDT"),
    "DOGE/USD": ("DOGEUSDT", "DOGE-USD", "XDGUSD", "DOGE-USDT", "DOGEUSDT"),
    "XRP/USD": ("XRPUSDT", "XRP-USD", "XRPUSD", "XRP-USDT", "XRPUSDT"),
    "ADA/USD": ("ADAUSDT", "ADA-USD", "ADAUSD", "ADA-USDT", "ADAUSDT"),
}


def normalize_symbol_pair(symbol: str) -> Tuple[str, str, str]:
    """Legacy backward compatibility: returns (canonical, binance_sym, coinbase_sym)."""
    norm = resolve_venue_symbols(symbol)
    return norm["canonical"], norm["binance"], norm["coinbase"]


def resolve_venue_symbols(symbol: str) -> Dict[str, str]:
    """
    Resolve any input symbol string (e.g. 'BTC', 'BTC/USD', 'AAPL') into
    canonical format and specific venue ticker representations.
    """
    s = symbol.upper().strip()

    # Check Equities
    if s in KNOWN_EQUITIES:
        return {
            "canonical": s,
            "type": "EQUITY",
            "yahoo": KNOWN_EQUITIES[s],
            "binance": "",
            "coinbase": "",
            "kraken": "",
            "okx": "",
            "bybit": "",
        }

    # Normalize crypto keys (e.g. 'BTCUSD', 'BTC-USD', 'BTC')
    key = s.replace("/", "").replace("-", "")
    if not key.endswith("USD") and not key.endswith("USDT") and f"{key}/USD" in CRYPTO_VENUE_MAP:
        key = f"{key}/USD"
    elif f"{s}/USD" in CRYPTO_VENUE_MAP:
        key = f"{s}/USD"
    elif "/" in s and s in CRYPTO_VENUE_MAP:
        key = s
    else:
        # Match against dictionary prefixes
        found = None
        for c_sym in CRYPTO_VENUE_MAP:
            base = c_sym.split("/")[0]
            if key.startswith(base):
                found = c_sym
                break
        key = found or (f"{s}/USD" if "/" not in s else s)

    if key in CRYPTO_VENUE_MAP:
        b_sym, c_sym, k_sym, o_sym, by_sym = CRYPTO_VENUE_MAP[key]
        return {
            "canonical": key,
            "type": "CRYPTO",
            "binance": b_sym,
            "coinbase": c_sym,
            "kraken": k_sym,
            "okx": o_sym,
            "bybit": by_sym,
            "yahoo": "",
        }

    # Default fallback
    canon = f"{s}/USD" if "/" not in s else s
    clean = canon.replace("/", "").replace("-", "")
    return {
        "canonical": canon,
        "type": "CRYPTO",
        "binance": clean if clean.endswith("USDT") else f"{clean}USDT",
        "coinbase": canon.replace("/", "-"),
        "kraken": clean.replace("USDT", "USD"),
        "okx": f"{clean.replace('USDT', '')}-USDT",
        "bybit": clean if clean.endswith("USDT") else f"{clean}USDT",
        "yahoo": "",
    }


class LiveConnector:
    """
    Multi-venue live market connector.
    Connects to Binance, Coinbase, Kraken, OKX, Bybit, and Global Equities.
    """

    def __init__(self, timeout: float = 4.0):
        self.timeout = timeout
        self._ctx = ssl.create_default_context()
        self._headers = {"User-Agent": "MDRAP-MarketData/1.0 (Finance-Infrastructure)"}

    def _get_json(self, url: str) -> Optional[dict]:
        """Fetch and decode JSON from public HTTP endpoint with timeout & error shielding."""
        try:
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    # 1. Binance
    def fetch_binance_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Binance public API."""
        mapping = resolve_venue_symbols(symbol)
        if not mapping["binance"]:
            return None
        url = BINANCE_TICKER_URL.format(symbol=mapping["binance"])
        t_recv = time.time()
        data = self._get_json(url)
        if not data or "bidPrice" not in data or "askPrice" not in data:
            return None

        try:
            bid_p = float(data["bidPrice"])
            bid_s = float(data.get("bidQty", 1.0))
            ask_p = float(data["askPrice"])
            ask_s = float(data.get("askQty", 1.0))

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.005,
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

    # 2. Coinbase
    def fetch_coinbase_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Coinbase public API."""
        mapping = resolve_venue_symbols(symbol)
        if not mapping["coinbase"]:
            return None
        url = COINBASE_TICKER_URL.format(symbol=mapping["coinbase"])
        t_recv = time.time()
        data = self._get_json(url)
        if not data or "bid" not in data or "ask" not in data:
            return None

        try:
            bid_p = float(data["bid"])
            ask_p = float(data["ask"])
            size = float(data.get("size", 1.0))

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.008,
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

    # 3. Kraken
    def fetch_kraken_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Kraken public API."""
        mapping = resolve_venue_symbols(symbol)
        if not mapping["kraken"]:
            return None
        url = KRAKEN_TICKER_URL.format(symbol=mapping["kraken"])
        t_recv = time.time()
        data = self._get_json(url)
        if not data or not data.get("result"):
            return None

        try:
            stats = list(data["result"].values())[0]
            bid_p = float(stats["b"][0])
            ask_p = float(stats["a"][0])
            bid_s = float(stats["b"][2])
            ask_s = float(stats["a"][2])

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.010,
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": bid_s,
                "ask_size": ask_s,
            }
            return RawEvent(
                source="KRAKEN",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-kraken-{next(_raw_counter)}",
            )
        except Exception:
            return None

    # 4. OKX
    def fetch_okx_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from OKX public API."""
        mapping = resolve_venue_symbols(symbol)
        if not mapping["okx"]:
            return None
        url = OKX_TICKER_URL.format(symbol=mapping["okx"])
        t_recv = time.time()
        data = self._get_json(url)
        if not data or not data.get("data"):
            return None

        try:
            item = data["data"][0]
            bid_p = float(item["bidPx"])
            ask_p = float(item["askPx"])
            bid_s = float(item.get("bidSz", 1.0))
            ask_s = float(item.get("askSz", 1.0))

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.007,
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": bid_s,
                "ask_size": ask_s,
            }
            return RawEvent(
                source="OKX",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-okx-{next(_raw_counter)}",
            )
        except Exception:
            return None

    # 5. Bybit
    def fetch_bybit_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch current top of book quote from Bybit public API."""
        mapping = resolve_venue_symbols(symbol)
        if not mapping["bybit"]:
            return None
        url = BYBIT_TICKER_URL.format(symbol=mapping["bybit"])
        t_recv = time.time()
        data = self._get_json(url)
        if not data or not data.get("result", {}).get("list"):
            return None

        try:
            item = data["result"]["list"][0]
            bid_p = float(item["bid1Price"])
            ask_p = float(item["ask1Price"])
            bid_s = float(item.get("bid1Size", 1.0))
            ask_s = float(item.get("ask1Size", 1.0))

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.006,
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": bid_s,
                "ask_size": ask_s,
            }
            return RawEvent(
                source="BYBIT",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-bybit-{next(_raw_counter)}",
            )
        except Exception:
            return None

    # 6. Global Equities (Yahoo Finance)
    def fetch_equity_quote(self, symbol: str) -> Optional[RawEvent]:
        """Fetch real-time equity or commodity quote (AAPL, NVDA, TSLA, SPY, Gold)."""
        mapping = resolve_venue_symbols(symbol)
        ticker = mapping["yahoo"] or mapping["canonical"]
        url = YAHOO_CHART_URL.format(symbol=ticker)
        t_recv = time.time()
        data = self._get_json(url)
        if not data or not data.get("chart", {}).get("result"):
            return None

        try:
            meta = data["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            # Estimate tight consolidated spread around market price if market is closed
            bid_p = round(float(meta.get("bid", price - 0.02)), 2)
            ask_p = round(float(meta.get("ask", price + 0.02)), 2)
            vol = float(meta.get("regularMarketVolume", 1000.0))

            payload = {
                "instrument": mapping["canonical"],
                "event_type": "QUOTE",
                "exchange_ts": t_recv - 0.015,
                "sequence": next(_seq_counter),
                "bid": bid_p,
                "ask": ask_p,
                "bid_size": 100.0,
                "ask_size": 100.0,
            }
            return RawEvent(
                source="EQUITIES",
                payload=payload,
                receive_timestamp=t_recv,
                raw_id=f"live-equities-{next(_raw_counter)}",
            )
        except Exception:
            return None

    def fetch_quote(self, symbol: str, source: str) -> Optional[RawEvent]:
        """Dispatch quote fetch by source venue name."""
        src = source.upper()
        if src == "BINANCE":
            return self.fetch_binance_quote(symbol)
        elif src == "COINBASE":
            return self.fetch_coinbase_quote(symbol)
        elif src == "KRAKEN":
            return self.fetch_kraken_quote(symbol)
        elif src == "OKX":
            return self.fetch_okx_quote(symbol)
        elif src == "BYBIT":
            return self.fetch_bybit_quote(symbol)
        elif src in ("EQUITIES", "YAHOO"):
            return self.fetch_equity_quote(symbol)
        return None

    def fetch_snapshot(self, symbol: str) -> List[RawEvent]:
        """Fetch current top of book quotes across all active venues concurrently in parallel."""
        import concurrent.futures
        mapping = resolve_venue_symbols(symbol)
        if mapping["type"] == "EQUITY":
            raw_eq = self.fetch_equity_quote(symbol)
            return [raw_eq] if raw_eq else []

        fetchers = [
            self.fetch_binance_quote,
            self.fetch_coinbase_quote,
            self.fetch_kraken_quote,
            self.fetch_okx_quote,
            self.fetch_bybit_quote,
        ]
        events: List[RawEvent] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(fetchers)) as executor:
            future_to_fetcher = {executor.submit(f, symbol): f for f in fetchers}
            for future in concurrent.futures.as_completed(future_to_fetcher):
                try:
                    res = future.result()
                    if res:
                        events.append(res)
                except Exception:
                    pass
        return events

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
        count = 0

        while True:
            for sym in symbols:
                sym_info = resolve_venue_symbols(sym)
                if sym_info["type"] == "EQUITY":
                    venue_list = ["EQUITIES"]
                else:
                    venue_list = [s.upper() for s in sources] if sources else [
                        "BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"
                    ]

                for v in venue_list:
                    raw = self.fetch_quote(sym, v)
                    if raw:
                        yield raw
                        count += 1
                        if limit and count >= limit:
                            return

            time.sleep(poll_interval_s)
