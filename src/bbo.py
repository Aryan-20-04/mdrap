"""
Synthetic Consolidated BBO (Best Bid & Offer / NBBO) Engine for MDRAP.

Aggregates quotes across multiple active market feeds, computes the global
tightest top-of-book, detects cross-exchange locked/crossed markets, prunes
unhealthy sources via Watchdog integration, and tracks venue price attribution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional

from models import CanonicalEvent, EventType, QualityStatus


@dataclass(slots=True)
class ConsolidatedBBO:
    instrument_id: str
    best_bid: float
    best_bid_size: float
    best_bid_source: str
    best_ask: float
    best_ask_size: float
    best_ask_source: str
    spread: float
    mid_price: float
    is_crossed: bool
    is_locked: bool
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "best_bid": self.best_bid,
            "best_bid_size": self.best_bid_size,
            "best_bid_source": self.best_bid_source,
            "best_ask": self.best_ask,
            "best_ask_size": self.best_ask_size,
            "best_ask_source": self.best_ask_source,
            "spread": round(self.spread, 4),
            "mid_price": round(self.mid_price, 4),
            "is_crossed": self.is_crossed,
            "is_locked": self.is_locked,
            "timestamp": self.timestamp,
        }


class BBOEngine:
    """
    Multi-Venue Consolidated Order Book Engine.
    """

    def __init__(self, quote_ttl_s: float = 2.0, watchdog: Optional[Any] = None):
        self.quote_ttl_s = quote_ttl_s
        self.watchdog = watchdog
        # _books[instrument_id][source] = CanonicalEvent
        self._books: Dict[str, Dict[str, CanonicalEvent]] = {}
        # Latest consolidated top of book per instrument
        self._current_bbos: Dict[str, ConsolidatedBBO] = {}
        # Venue attribution counters: source -> {'bid_count': int, 'ask_count': int, 'both_count': int}
        self._attribution: Dict[str, Dict[str, int]] = {}
        self._update_count = 0
        self._crossed_count = 0
        self._locked_count = 0

    def _is_source_eligible(self, source: str) -> bool:
        if not self.watchdog:
            return True
        if hasattr(self.watchdog, "is_source_active"):
            return self.watchdog.is_source_active(source)
        return True

    def observe(self, event: CanonicalEvent) -> Optional[ConsolidatedBBO]:
        """
        Ingest a canonical quote event and compute the updated Consolidated BBO.
        Only processes QUOTE events with valid bid and ask prices.
        """
        if event.event_type != EventType.QUOTE:
            return None

        # Never poison consolidated book with INVALID quotes
        if event.quality_status == QualityStatus.INVALID:
            return None

        if event.bid_price is None or event.ask_price is None:
            return None

        inst = event.instrument_id
        src = event.source
        market_now = event.exchange_timestamp

        inst_book = self._books.setdefault(inst, {})

        # If source is currently eligible, update its quote
        if self._is_source_eligible(src):
            inst_book[src] = event
        else:
            # Source was degraded/silent/blocked, evict its quote
            inst_book.pop(src, None)

        # Prune expired quotes (based on market time) or quotes from degraded sources
        dead_sources = []
        for s, q in inst_book.items():
            if not self._is_source_eligible(s):
                dead_sources.append(s)
            elif (market_now - q.exchange_timestamp) > self.quote_ttl_s:
                dead_sources.append(s)

        for s in dead_sources:
            inst_book.pop(s, None)

        if not inst_book:
            return None

        best_bid = -1.0
        best_bid_size = 0.0
        best_bid_src = ""
        best_ask = float("inf")
        best_ask_size = 0.0
        best_ask_src = ""

        for s, q in inst_book.items():
            bp = q.bid_price
            if bp is not None:
                if bp > best_bid or (bp == best_bid and (q.bid_size or 0.0) > best_bid_size):
                    best_bid = bp
                    best_bid_size = q.bid_size or 0.0
                    best_bid_src = s
            ap = q.ask_price
            if ap is not None:
                if ap < best_ask or (ap == best_ask and (q.ask_size or 0.0) > best_ask_size):
                    best_ask = ap
                    best_ask_size = q.ask_size or 0.0
                    best_ask_src = s

        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2.0
        is_crossed = best_bid > best_ask
        is_locked = (best_bid == best_ask)

        bbo = ConsolidatedBBO(
            instrument_id=inst,
            best_bid=best_bid,
            best_bid_size=best_bid_size,
            best_bid_source=best_bid_src,
            best_ask=best_ask,
            best_ask_size=best_ask_size,
            best_ask_source=best_ask_src,
            spread=spread,
            mid_price=mid_price,
            is_crossed=is_crossed,
            is_locked=is_locked,
            timestamp=market_now,
        )

        self._current_bbos[inst] = bbo
        self._update_count += 1
        if is_crossed:
            self._crossed_count += 1
        if is_locked:
            self._locked_count += 1

        # Track venue contribution statistics
        for venue in (best_bid_src, best_ask_src):
            if venue not in self._attribution:
                self._attribution[venue] = {"bid_count": 0, "ask_count": 0, "both_count": 0}

        if best_bid_src == best_ask_src:
            self._attribution[best_bid_src]["both_count"] += 1
            self._attribution[best_bid_src]["bid_count"] += 1
            self._attribution[best_bid_src]["ask_count"] += 1
        else:
            self._attribution[best_bid_src]["bid_count"] += 1
            self._attribution[best_ask_src]["ask_count"] += 1

        return bbo

    def current_bbo(self, instrument_id: str) -> Optional[ConsolidatedBBO]:
        return self._current_bbos.get(instrument_id)

    def all_bbos(self) -> Dict[str, ConsolidatedBBO]:
        return dict(self._current_bbos)

    def venue_attribution(self) -> Dict[str, dict]:
        """
        Return venue attribution metrics with percentage shares.
        """
        total_updates = max(1, self._update_count)
        result = {}
        for src, counts in self._attribution.items():
            result[src] = {
                "bid_count": counts["bid_count"],
                "bid_pct": round(100.0 * counts["bid_count"] / total_updates, 2),
                "ask_count": counts["ask_count"],
                "ask_pct": round(100.0 * counts["ask_count"] / total_updates, 2),
                "both_count": counts["both_count"],
                "both_pct": round(100.0 * counts["both_count"] / total_updates, 2),
            }
        return result

    def stats(self) -> dict:
        return {
            "total_updates": self._update_count,
            "crossed_markets": self._crossed_count,
            "locked_markets": self._locked_count,
            "instruments_covered": len(self._current_bbos),
            "venues_contributing": len(self._attribution),
        }
