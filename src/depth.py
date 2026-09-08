"""
Consolidated Level-2 (L2) Market Depth Engine for MDRAP (Spec §18).

Aggregates multi-level order books across multiple venues (Binance, Coinbase,
Kraken, OKX, Bybit), merges identical price levels into consolidated rungs,
calculates volume-weighted micro-price, computes order flow imbalance (OFI),
detects cross-exchange depth arbitrage, and computes real-time VWAP execution
and slippage curves across multi-tier order sizing slices.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from models import CanonicalEvent, EventType, QualityStatus, RawEvent


@dataclass(slots=True)
class DepthLevel:
    price: float
    size: float
    venue: str
    order_count: int = 1

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "size": self.size,
            "venue": self.venue,
            "order_count": self.order_count,
        }


@dataclass(slots=True)
class AggregatedLevel:
    """
    Consolidated order book price level aggregating depth across multiple venues.
    """
    price: float
    total_size: float
    venue_sizes: Dict[str, float]
    order_count: int = 1
    cumulative_size: float = 0.0
    cumulative_notional: float = 0.0

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "total_size": round(self.total_size, 4),
            "venue_sizes": {v: round(s, 4) for v, s in self.venue_sizes.items()},
            "order_count": self.order_count,
            "cumulative_size": round(self.cumulative_size, 4),
            "cumulative_notional": round(self.cumulative_notional, 2),
        }


@dataclass
class VWAPSlice:
    """
    Real-time VWAP execution slice for a specified order target size.
    Simulates walking the consolidated order book ladder to estimate market impact,
    slippage vs NBBO, and cross-venue routing attribution without executing orders.
    """
    side: str                          # 'BUY' or 'SELL'
    target_size: float
    filled_size: float
    vwap_price: float
    slippage_bps: float                # Basis points vs NBBO best ask (BUY) or best bid (SELL)
    slippage_dollars: float            # |VWAP - BestPrice|
    effective_spread_bps: float        # (VWAP - mid_price) / mid_price * 10,000
    is_fully_filled: bool
    venue_breakdown: Dict[str, float] = field(default_factory=dict)
    total_notional: float = 0.0

    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "target_size": self.target_size,
            "filled_size": round(self.filled_size, 4),
            "vwap_price": round(self.vwap_price, 4),
            "slippage_bps": round(self.slippage_bps, 2),
            "slippage_dollars": round(self.slippage_dollars, 4),
            "effective_spread_bps": round(self.effective_spread_bps, 2),
            "is_fully_filled": self.is_fully_filled,
            "venue_breakdown": {v: round(s, 4) for v, s in self.venue_breakdown.items()},
            "total_notional": round(self.total_notional, 2),
        }


@dataclass
class VWAPCurve:
    """
    Complete real-time multi-tier VWAP execution curve across both sides of the book.
    """
    instrument_id: str
    timestamp: float
    mid_price: float
    best_bid: float
    best_ask: float
    buy_slices: List[VWAPSlice] = field(default_factory=list)
    sell_slices: List[VWAPSlice] = field(default_factory=list)
    depth_10bps: Tuple[float, float] = (0.0, 0.0)   # (bid_notional, ask_notional)
    depth_50bps: Tuple[float, float] = (0.0, 0.0)
    depth_100bps: Tuple[float, float] = (0.0, 0.0)

    def to_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "timestamp": self.timestamp,
            "mid_price": round(self.mid_price, 4),
            "best_bid": round(self.best_bid, 4),
            "best_ask": round(self.best_ask, 4),
            "buy_slices": [s.to_dict() for s in self.buy_slices],
            "sell_slices": [s.to_dict() for s in self.sell_slices],
            "depth_10bps": {"bid": round(self.depth_10bps[0], 2), "ask": round(self.depth_10bps[1], 2)},
            "depth_50bps": {"bid": round(self.depth_50bps[0], 2), "ask": round(self.depth_50bps[1], 2)},
            "depth_100bps": {"bid": round(self.depth_100bps[0], 2), "ask": round(self.depth_100bps[1], 2)},
        }


@dataclass
class ConsolidatedLadder:
    instrument_id: str
    bids: List[DepthLevel]                     # Sorted descending by price (highest bid first)
    asks: List[DepthLevel]                     # Sorted ascending by price (lowest ask first)
    timestamp: float
    micro_price: float                         # Volume-weighted top-of-book mid-price
    imbalance_ratio: float                     # Order book imbalance [-1.0, 1.0]
    is_crossed: bool                           # Global book crossed indicator
    crossed_opportunities: List[dict] = field(default_factory=list)
    aggregated_bids: List[AggregatedLevel] = field(default_factory=list)
    aggregated_asks: List[AggregatedLevel] = field(default_factory=list)
    total_bid_notional: float = 0.0
    total_ask_notional: float = 0.0
    vwap_curve: Optional[VWAPCurve] = None
    ofi: float = 0.0                           # Level-1 Order Flow Imbalance for current update
    cumulative_ofi: float = 0.0                # Running sum of OFI
    cvd: float = 0.0                           # Cumulative Volume Delta

    def depth_within_bps(self, bps: float) -> Tuple[float, float]:
        """
        Calculate total available liquidity in notional currency within +/- bps of mid-price.
        Returns: (bid_notional, ask_notional)
        """
        best_bid = self.bids[0].price if self.bids else 0.0
        best_ask = self.asks[0].price if self.asks else 0.0
        mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
        if mid <= 0.0:
            return (0.0, 0.0)

        bid_floor = mid * (1.0 - (bps / 10000.0))
        ask_ceiling = mid * (1.0 + (bps / 10000.0))

        bid_notional = sum(b.price * b.size for b in self.bids if b.price >= bid_floor)
        ask_notional = sum(a.price * a.size for a in self.asks if a.price <= ask_ceiling)
        return (bid_notional, ask_notional)

    def compute_vwap(self, side: str, target_size: float) -> VWAPSlice:
        """
        Simulate an institutional order of target_size, walking the consolidated book
        to compute volume-weighted average fill price, slippage in bps, and venue routing attribution.
        """
        side_norm = side.upper()
        if side_norm not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {side}. Must be 'BUY' or 'SELL'")

        levels = self.asks if side_norm == "BUY" else self.bids
        best_bid = self.bids[0].price if self.bids else 0.0
        best_ask = self.asks[0].price if self.asks else 0.0
        best_price = best_ask if side_norm == "BUY" else best_bid
        mid_price = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else best_price

        remaining = float(target_size)
        cum_notional = 0.0
        venue_breakdown: Dict[str, float] = {}

        for lvl in levels:
            if remaining <= 1e-9:
                break
            fill = min(remaining, lvl.size)
            cum_notional += lvl.price * fill
            venue_breakdown[lvl.venue] = venue_breakdown.get(lvl.venue, 0.0) + fill
            remaining -= fill

        filled = target_size - remaining
        vwap_price = (cum_notional / filled) if filled > 0 else best_price

        if side_norm == "BUY":
            slippage_dollars = max(0.0, vwap_price - best_price)
            slippage_bps = (slippage_dollars / best_price * 10000.0) if best_price > 0 else 0.0
            eff_spread_bps = ((vwap_price - mid_price) / mid_price * 10000.0) if mid_price > 0 else 0.0
        else:
            slippage_dollars = max(0.0, best_price - vwap_price)
            slippage_bps = (slippage_dollars / best_price * 10000.0) if best_price > 0 else 0.0
            eff_spread_bps = ((mid_price - vwap_price) / mid_price * 10000.0) if mid_price > 0 else 0.0

        return VWAPSlice(
            side=side_norm,
            target_size=target_size,
            filled_size=filled,
            vwap_price=vwap_price,
            slippage_bps=slippage_bps,
            slippage_dollars=slippage_dollars,
            effective_spread_bps=eff_spread_bps,
            is_fully_filled=(remaining <= 1e-9),
            venue_breakdown=venue_breakdown,
            total_notional=cum_notional,
        )

    def compute_vwap_curve(self, sizes: Optional[List[float]] = None) -> VWAPCurve:
        """
        Compute real-time VWAP curve for institutional benchmark sizing tranches.
        """
        if sizes is None:
            sizes = [1.0, 5.0, 10.0, 25.0, 50.0]

        best_bid = self.bids[0].price if self.bids else 0.0
        best_ask = self.asks[0].price if self.asks else 0.0
        mid_price = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0

        buy_slices = [self.compute_vwap("BUY", s) for s in sizes]
        sell_slices = [self.compute_vwap("SELL", s) for s in sizes]

        return VWAPCurve(
            instrument_id=self.instrument_id,
            timestamp=self.timestamp,
            mid_price=mid_price,
            best_bid=best_bid,
            best_ask=best_ask,
            buy_slices=buy_slices,
            sell_slices=sell_slices,
            depth_10bps=self.depth_within_bps(10.0),
            depth_50bps=self.depth_within_bps(50.0),
            depth_100bps=self.depth_within_bps(100.0),
        )

    def to_dict(self) -> dict:
        d = {
            "instrument_id": self.instrument_id,
            "bids": [b.to_dict() for b in self.bids],
            "asks": [a.to_dict() for a in self.asks],
            "aggregated_bids": [b.to_dict() for b in self.aggregated_bids],
            "aggregated_asks": [a.to_dict() for a in self.aggregated_asks],
            "timestamp": self.timestamp,
            "micro_price": round(self.micro_price, 4),
            "imbalance_ratio": round(self.imbalance_ratio, 4),
            "is_crossed": self.is_crossed,
            "crossed_opportunities": self.crossed_opportunities,
            "total_bid_notional": round(self.total_bid_notional, 2),
            "total_ask_notional": round(self.total_ask_notional, 2),
            "ofi": round(self.ofi, 4),
            "cumulative_ofi": round(self.cumulative_ofi, 4),
            "cvd": round(self.cvd, 4),
        }
        if self.vwap_curve:
            d["vwap_curve"] = self.vwap_curve.to_dict()
        return d


def _aggregate_levels(levels: List[DepthLevel], is_descending: bool) -> List[AggregatedLevel]:
    """
    Coalesce individual venue depth levels into unified price rungs with venue attribution.
    """
    price_map: Dict[float, Dict[str, Any]] = {}
    for lvl in levels:
        p = lvl.price
        if p not in price_map:
            price_map[p] = {
                "total_size": 0.0,
                "venue_sizes": {},
                "order_count": 0,
            }
        entry = price_map[p]
        entry["total_size"] += lvl.size
        entry["venue_sizes"][lvl.venue] = entry["venue_sizes"].get(lvl.venue, 0.0) + lvl.size
        entry["order_count"] += lvl.order_count

    sorted_prices = sorted(price_map.keys(), reverse=is_descending)
    agg_levels = []
    cum_size = 0.0
    cum_notional = 0.0

    for p in sorted_prices:
        entry = price_map[p]
        sz = entry["total_size"]
        cum_size += sz
        cum_notional += p * sz
        agg_levels.append(AggregatedLevel(
            price=p,
            total_size=sz,
            venue_sizes=entry["venue_sizes"],
            order_count=entry["order_count"],
            cumulative_size=cum_size,
            cumulative_notional=cum_notional,
        ))
    return agg_levels


class ConsolidatedDepthEngine:
    """
    Multi-Venue Level-2 Order Book Aggregation & VWAP Slicing Engine.
    """

    def __init__(
        self,
        depth_ttl_s: float = 3.0,
        max_levels_per_side: int = 10,
        watchdog: Optional[Any] = None,
    ):
        self.depth_ttl_s = depth_ttl_s
        self.max_levels_per_side = max_levels_per_side
        self.watchdog = watchdog
        # _venue_books[instrument][venue] = {"bids": [[price, size], ...], "asks": [[price, size], ...], "updated_at": ts}
        self._venue_books: Dict[str, Dict[str, dict]] = {}
        # Cached current ladders per instrument
        self._current_ladders: Dict[str, ConsolidatedLadder] = {}
        # Pre-serialized wire JSON byte buffers per instrument (zero-allocation fastpath)
        self._cached_depth_json: Dict[str, bytes] = {}
        self._cached_vwap_json: Dict[str, bytes] = {}
        self._total_updates = 0
        self._crossed_depth_count = 0
        self._prev_tob: Dict[str, Tuple[float, float, float, float]] = {}
        self._cum_ofi: Dict[str, float] = {}
        self._cum_cvd: Dict[str, float] = {}

    def observe_trade(
        self,
        instrument: str,
        price: float,
        quantity: float,
        side: Optional[str] = None,
    ) -> float:
        """
        Record a trade event and update Cumulative Volume Delta (CVD).
        Uses trade side if provided, or tick/quote rule relative to top-of-book.
        """
        delta = 0.0
        if side:
            s = str(side).upper()
            if s in ("BUY", "B", "1"):
                delta = quantity
            elif s in ("SELL", "S", "-1"):
                delta = -quantity
        else:
            ladder = self._current_ladders.get(instrument)
            if ladder and ladder.bids and ladder.asks:
                best_bid = ladder.bids[0].price
                best_ask = ladder.asks[0].price
                mid = (best_bid + best_ask) / 2.0
                if price >= best_ask:
                    delta = quantity
                elif price <= best_bid:
                    delta = -quantity
                elif price >= mid:
                    delta = quantity
                else:
                    delta = -quantity
            else:
                delta = quantity

        self._cum_cvd[instrument] = self._cum_cvd.get(instrument, 0.0) + delta
        return self._cum_cvd[instrument]

    def _is_source_eligible(self, source: str) -> bool:
        if not self.watchdog:
            return True
        if hasattr(self.watchdog, "is_source_active"):
            return self.watchdog.is_source_active(source)
        return True

    def observe(self, event: CanonicalEvent | RawEvent) -> Optional[ConsolidatedLadder]:
        """
        Observe a market event (either RawEvent or CanonicalEvent), extract
        its depth levels, and update the global consolidated order book.
        """
        if isinstance(event, RawEvent):
            src = event.source.upper()
            p = event.payload if isinstance(event.payload, dict) else {}
            inst = str(p.get("instrument", "UNKNOWN"))
            try:
                t_val = p.get("exchange_ts", event.receive_timestamp)
                t_event = float(t_val) if t_val is not None else float(event.receive_timestamp)
            except (ValueError, TypeError):
                t_event = float(event.receive_timestamp)

            evt_t = str(p.get("type") or p.get("event_type") or "").upper()
            if evt_t == "TRADE" or ("price" in p and "quantity" in p and "bids" not in p and "asks" not in p):
                try:
                    px = float(p["price"])
                    qty = float(p.get("quantity", 1.0))
                    if px > 0 and qty > 0:
                        self.observe_trade(inst, px, qty, p.get("side"))
                except (ValueError, TypeError):
                    pass

            bids_raw = p.get("bids", [])
            asks_raw = p.get("asks", [])
            # Fallback to single top-of-book level if bids/asks lists are missing
            try:
                if not bids_raw and "bid" in p and p["bid"] is not None:
                    bids_raw = [[float(p["bid"]), float(p.get("bid_size", 1.0))]]
                if not asks_raw and "ask" in p and p["ask"] is not None:
                    asks_raw = [[float(p["ask"]), float(p.get("ask_size", 1.0))]]
            except (ValueError, TypeError):
                return None
        elif isinstance(event, CanonicalEvent):
            if event.quality_status == QualityStatus.INVALID:
                return None
            src = event.source.upper()
            inst = event.instrument_id
            t_event = event.exchange_timestamp
            if event.event_type == EventType.TRADE and event.price is not None:
                q = event.quantity or 1.0
                if event.price > 0 and q > 0:
                    self.observe_trade(inst, event.price, q, None)
            bids_raw = [[event.bid_price, event.bid_size or 1.0]] if event.bid_price is not None else []
            asks_raw = [[event.ask_price, event.ask_size or 1.0]] if event.ask_price is not None else []
        else:
            return None

        if not self._is_source_eligible(src):
            return None

        inst_books = self._venue_books.setdefault(inst, {})
        if bids_raw or asks_raw:
            inst_books[src] = {
                "bids": bids_raw,
                "asks": asks_raw,
                "updated_at": t_event,
            }

        # Prune expired or inactive venue books
        dead_venues = []
        for v, book in inst_books.items():
            if not self._is_source_eligible(v):
                dead_venues.append(v)
            elif (t_event - book["updated_at"]) > self.depth_ttl_s:
                dead_venues.append(v)

        for v in dead_venues:
            inst_books.pop(v, None)

        if not inst_books:
            return None

        # Aggregate and merge all bids and asks across all active venues
        all_bids: List[DepthLevel] = []
        all_asks: List[DepthLevel] = []

        for v, book in inst_books.items():
            for row in book.get("bids", []):
                try:
                    p_val = float(row[0])
                    s_val = float(row[1])
                    if p_val > 0 and s_val > 0:
                        all_bids.append(DepthLevel(price=p_val, size=s_val, venue=v))
                except (ValueError, IndexError):
                    continue

            for row in book.get("asks", []):
                try:
                    p_val = float(row[0])
                    s_val = float(row[1])
                    if p_val > 0 and s_val > 0:
                        all_asks.append(DepthLevel(price=p_val, size=s_val, venue=v))
                except (ValueError, IndexError):
                    continue

        if not all_bids or not all_asks:
            return None

        # Sort bids descending (highest bid first) and asks ascending (lowest ask first)
        all_bids.sort(key=lambda x: x.price, reverse=True)
        all_asks.sort(key=lambda x: x.price, reverse=False)

        merged_bids = all_bids[:self.max_levels_per_side]
        merged_asks = all_asks[:self.max_levels_per_side]

        # Calculate Micro-Price (Volume-Weighted Mid-Price)
        best_bid = merged_bids[0].price
        best_bid_sz = merged_bids[0].size
        best_ask = merged_asks[0].price
        best_ask_sz = merged_asks[0].size

        if (best_bid_sz + best_ask_sz) > 0:
            micro_price = (best_bid * best_ask_sz + best_ask * best_bid_sz) / (best_bid_sz + best_ask_sz)
        else:
            micro_price = (best_bid + best_ask) / 2.0

        # Calculate Static Book Imbalance Ratio across aggregated depth
        tot_bid_vol = sum(b.size for b in merged_bids)
        tot_ask_vol = sum(a.size for a in merged_asks)
        if (tot_bid_vol + tot_ask_vol) > 0:
            imbalance = (tot_bid_vol - tot_ask_vol) / (tot_bid_vol + tot_ask_vol)
        else:
            imbalance = 0.0

        # Calculate Level-1 Order Flow Imbalance (OFI) - Cont, Kukanov & Stoikov (2014)
        prev_tob = self._prev_tob.get(inst)
        if prev_tob is not None:
            prev_bb, prev_bbs, prev_ba, prev_bas = prev_tob
            if best_bid > prev_bb:
                delta_w_b = best_bid_sz
            elif best_bid == prev_bb:
                delta_w_b = best_bid_sz - prev_bbs
            else:
                delta_w_b = -prev_bbs

            if best_ask < prev_ba:
                delta_w_a = -best_ask_sz
            elif best_ask == prev_ba:
                delta_w_a = best_ask_sz - prev_bas
            else:
                delta_w_a = prev_bas

            delta_ofi = delta_w_b - delta_w_a
        else:
            delta_ofi = 0.0

        self._prev_tob[inst] = (best_bid, best_bid_sz, best_ask, best_ask_sz)
        self._cum_ofi[inst] = self._cum_ofi.get(inst, 0.0) + delta_ofi
        cum_ofi = self._cum_ofi[inst]
        cum_cvd = self._cum_cvd.get(inst, 0.0)

        # Detect cross-exchange depth arbitrage opportunities
        crossed_opps = []
        for b in merged_bids:
            for a in merged_asks:
                if b.price > a.price and b.venue != a.venue:
                    crossed_opps.append({
                        "bid_venue": b.venue,
                        "bid_price": b.price,
                        "bid_size": b.size,
                        "ask_venue": a.venue,
                        "ask_price": a.price,
                        "ask_size": a.size,
                        "arb_spread": round(b.price - a.price, 4),
                        "max_volume": min(b.size, a.size),
                    })

        is_crossed = len(crossed_opps) > 0
        if is_crossed:
            self._crossed_depth_count += 1
        self._total_updates += 1

        # Price-aggregated ladders (merges identical price rungs)
        agg_bids = _aggregate_levels(all_bids, is_descending=True)[:self.max_levels_per_side]
        agg_asks = _aggregate_levels(all_asks, is_descending=False)[:self.max_levels_per_side]

        tot_bid_notional = sum(b.price * b.size for b in all_bids)
        tot_ask_notional = sum(a.price * a.size for a in all_asks)

        ladder = ConsolidatedLadder(
            instrument_id=inst,
            bids=merged_bids,
            asks=merged_asks,
            timestamp=t_event,
            micro_price=micro_price,
            imbalance_ratio=imbalance,
            is_crossed=is_crossed,
            crossed_opportunities=crossed_opps,
            aggregated_bids=agg_bids,
            aggregated_asks=agg_asks,
            total_bid_notional=tot_bid_notional,
            total_ask_notional=tot_ask_notional,
            ofi=round(delta_ofi, 4),
            cumulative_ofi=round(cum_ofi, 4),
            cvd=round(cum_cvd, 4),
        )
        ladder.vwap_curve = ladder.compute_vwap_curve()

        self._current_ladders[inst] = ladder

        # Pre-render wire JSON byte buffers (RCU pattern: atomic swap, zero-allocation reader fastpath)
        depth_dict = {"status": "OK", "symbol": inst, "depth": ladder.to_dict()}
        self._cached_depth_json[inst] = (json.dumps(depth_dict) + "\n").encode("utf-8")

        if ladder.vwap_curve:
            vwap_dict = {"status": "OK", "symbol": inst, "vwap_curve": ladder.vwap_curve.to_dict()}
            self._cached_vwap_json[inst] = (json.dumps(vwap_dict) + "\n").encode("utf-8")

        return ladder

    def current_ladder(self, instrument_id: str) -> Optional[ConsolidatedLadder]:
        return self._current_ladders.get(instrument_id)

    def get_ladder_wire_bytes(self, instrument_id: str) -> bytes:
        """Return pre-rendered UTF-8 JSON wire bytes for L2 depth with zero serialization overhead."""
        cached = self._cached_depth_json.get(instrument_id)
        if cached:
            return cached
        return (json.dumps({"status": "OK", "symbol": instrument_id, "depth": None}) + "\n").encode("utf-8")

    def current_vwap_curve(self, instrument_id: str, sizes: Optional[List[float]] = None) -> Optional[VWAPCurve]:
        ladder = self._current_ladders.get(instrument_id)
        if not ladder:
            return None
        if sizes:
            return ladder.compute_vwap_curve(sizes=sizes)
        return ladder.vwap_curve

    def get_vwap_wire_bytes(self, instrument_id: str, sizes: Optional[List[float]] = None) -> bytes:
        """Return pre-rendered UTF-8 JSON wire bytes for VWAP slicing curve."""
        if sizes is None:
            cached = self._cached_vwap_json.get(instrument_id)
            if cached:
                return cached
        curve = self.current_vwap_curve(instrument_id, sizes=sizes)
        return (json.dumps({"status": "OK", "symbol": instrument_id, "vwap_curve": curve.to_dict() if curve else None}) + "\n").encode("utf-8")

    def all_ladders(self) -> Dict[str, ConsolidatedLadder]:
        return dict(self._current_ladders)

    def stats(self) -> dict:
        return {
            "total_updates": self._total_updates,
            "crossed_depth_updates": self._crossed_depth_count,
            "instruments_tracked": len(self._current_ladders),
        }
