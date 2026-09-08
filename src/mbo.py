"""
Market-By-Order (Level-3 / L3 MBO) Matching Queue Engine for MDRAP (Spec §18, §26).

Implements institutional order lifecycle and matching queue mechanics (CME MDP 3.0, NASDAQ ITCH):
- Tracks individual resting orders by unique order_id
- Preserves exchange FIFO price-time queue priority
- Order modify semantics: size reductions preserve priority; price changes and size increases lose priority
- Microsecond queue position estimation (orders ahead, size ahead, queue rank)
- Real-time Level-3 to Level-2 (MBP) book projection
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Deque, Dict, List, Optional, Tuple


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(slots=True)
class RestingOrder:
    order_id: str
    instrument_id: str
    side: str                          # 'BUY' or 'SELL'
    price: float
    size: float
    venue: str = ""
    timestamp: float = 0.0
    priority: int = 0                  # Monotonic arrival sequence for FIFO rank

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "instrument": self.instrument_id,
            "side": self.side,
            "price": self.price,
            "size": round(self.size, 4),
            "venue": self.venue,
            "timestamp": self.timestamp,
            "priority": self.priority,
        }


@dataclass(slots=True)
class QueuePositionInfo:
    order_id: str
    instrument_id: str
    side: str
    price: float
    size: float
    orders_ahead: int
    size_ahead: float
    total_level_size: float
    total_level_orders: int
    queue_rank: int                     # 1-indexed rank in queue

    @property
    def fill_probability_pct(self) -> float:
        return round(
            max(0.0, 100.0 * (1.0 - (self.size_ahead / max(0.001, self.total_level_size)))), 2
        )

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "instrument": self.instrument_id,
            "side": self.side,
            "price": self.price,
            "size": round(self.size, 4),
            "orders_ahead": self.orders_ahead,
            "size_ahead": round(self.size_ahead, 4),
            "total_level_size": round(self.total_level_size, 4),
            "total_level_orders": self.total_level_orders,
            "queue_rank": self.queue_rank,
            "fill_probability_pct": self.fill_probability_pct,
        }


class PriceLevelQueue:
    """FIFO Queue of resting orders at a discrete price rung."""

    __slots__ = ("price", "order_ids", "total_size", "venue_sizes")

    def __init__(self, price: float):
        self.price = price
        self.order_ids: Deque[str] = collections.deque()
        self.total_size = 0.0
        self.venue_sizes: Dict[str, float] = {}

    @property
    def order_count(self) -> int:
        return len(self.order_ids)

    def add_order(self, order_id: str, size: float, venue: str) -> None:
        self.order_ids.append(order_id)
        self.total_size += size
        self.venue_sizes[venue] = self.venue_sizes.get(venue, 0.0) + size

    def remove_order(self, order_id: str, size: float, venue: str) -> bool:
        try:
            self.order_ids.remove(order_id)
            self.total_size = max(0.0, self.total_size - size)
            if venue in self.venue_sizes:
                self.venue_sizes[venue] = max(0.0, self.venue_sizes[venue] - size)
                if self.venue_sizes[venue] <= 1e-9:
                    del self.venue_sizes[venue]
            return True
        except ValueError:
            return False

    def reduce_order_size(self, size_reduction: float, venue: str) -> None:
        self.total_size = max(0.0, self.total_size - size_reduction)
        if venue in self.venue_sizes:
            self.venue_sizes[venue] = max(0.0, self.venue_sizes[venue] - size_reduction)


class OrderBookMBO:
    """
    Market-By-Order (L3) Order Book with FIFO Queue Priority & Level-2 Projection.
    """

    def __init__(self, instrument_id: str, max_depth_levels: int = 10):
        self.instrument_id = instrument_id
        self.max_depth_levels = max_depth_levels

        # Order lookup by order_id: O(1)
        self.orders: Dict[str, RestingOrder] = {}

        # Price level queues: price -> PriceLevelQueue
        self.bids: Dict[float, PriceLevelQueue] = {}
        self.asks: Dict[float, PriceLevelQueue] = {}

        self._priority_seq = 0
        self._total_adds = 0
        self._total_cancels = 0
        self._total_modifies = 0
        self._total_executes = 0

    def _next_priority(self) -> int:
        self._priority_seq += 1
        return self._priority_seq

    def order_add(
        self,
        order_id: str,
        side: str,
        price: float,
        size: float,
        venue: str = "",
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Add a new resting order to the book. Appends order to tail of price queue.
        """
        if order_id in self.orders or price <= 0 or size <= 0:
            return False

        t = timestamp if timestamp is not None else time.time()
        s = side.upper()
        p = round(price, 4)
        sz = round(size, 4)
        pri = self._next_priority()

        order = RestingOrder(
            order_id=order_id,
            instrument_id=self.instrument_id,
            side=s,
            price=p,
            size=sz,
            venue=venue,
            timestamp=t,
            priority=pri,
        )
        self.orders[order_id] = order

        book = self.bids if s in ("BUY", "BID") else self.asks
        if p not in book:
            book[p] = PriceLevelQueue(price=p)
        book[p].add_order(order_id, sz, venue)

        self._total_adds += 1
        return True

    def order_modify(
        self,
        order_id: str,
        new_size: float,
        new_price: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Modify an existing resting order.
        - Size reduction: Preserves FIFO queue priority.
        - Size increase or price change: Loses priority and moves to tail of queue.
        """
        order = self.orders.get(order_id)
        if not order:
            return False

        t = timestamp if timestamp is not None else time.time()
        target_price = round(new_price, 4) if new_price is not None else order.price
        target_size = round(new_size, 4)

        if target_size <= 0:
            return self.order_cancel(order_id)

        book = self.bids if order.side in ("BUY", "BID") else self.asks
        price_changed = abs(target_price - order.price) > 1e-6
        size_increased = target_size > order.size

        # Case 1: Pure size reduction at same price -> PRESERVE PRIORITY
        if not price_changed and not size_increased:
            reduction = order.size - target_size
            order.size = target_size
            order.timestamp = t
            if order.price in book:
                book[order.price].reduce_order_size(reduction, order.venue)
            self._total_modifies += 1
            return True

        # Case 2: Price changed or size increased -> LOSE PRIORITY (re-queue at tail)
        # Remove from current level
        if order.price in book:
            book[order.price].remove_order(order_id, order.size, order.venue)
            if book[order.price].order_count == 0:
                del book[order.price]

        # Update order state with new priority
        order.price = target_price
        order.size = target_size
        order.timestamp = t
        order.priority = self._next_priority()

        # Insert at tail of new price level
        if target_price not in book:
            book[target_price] = PriceLevelQueue(price=target_price)
        book[target_price].add_order(order_id, target_size, order.venue)

        self._total_modifies += 1
        return True

    def order_cancel(self, order_id: str) -> bool:
        """Cancel and remove a resting order from the book."""
        order = self.orders.pop(order_id, None)
        if not order:
            return False

        book = self.bids if order.side in ("BUY", "BID") else self.asks
        if order.price in book:
            book[order.price].remove_order(order_id, order.size, order.venue)
            if book[order.price].order_count == 0:
                del book[order.price]

        self._total_cancels += 1
        return True

    def order_execute(self, order_id: str, filled_size: float) -> Tuple[bool, float]:
        """
        Execute trade against resting order.
        Deducts filled_size in place, preserving FIFO priority for any remaining size.
        Returns (success, remaining_size).
        """
        order = self.orders.get(order_id)
        if not order or filled_size <= 0:
            return False, 0.0

        book = self.bids if order.side in ("BUY", "BID") else self.asks
        fill = min(order.size, round(filled_size, 4))
        remaining = max(0.0, order.size - fill)

        if remaining <= 1e-6:
            self.order_cancel(order_id)
            self._total_executes += 1
            return True, 0.0

        # Partial fill: update order size in place
        reduction = order.size - remaining
        order.size = remaining
        if order.price in book:
            book[order.price].reduce_order_size(reduction, order.venue)

        self._total_executes += 1
        return True, remaining

    def get_queue_position(self, order_id: str) -> Optional[QueuePositionInfo]:
        """
        Estimate exact FIFO queue position (orders ahead and size ahead) for an order.
        """
        order = self.orders.get(order_id)
        if not order:
            return None

        book = self.bids if order.side in ("BUY", "BID") else self.asks
        queue = book.get(order.price)
        if not queue:
            return None

        orders_ahead = 0
        size_ahead = 0.0
        rank = 1

        for oid in queue.order_ids:
            if oid == order_id:
                break
            ahead_order = self.orders.get(oid)
            if ahead_order:
                orders_ahead += 1
                size_ahead += ahead_order.size
                rank += 1

        return QueuePositionInfo(
            order_id=order_id,
            instrument_id=self.instrument_id,
            side=order.side,
            price=order.price,
            size=order.size,
            orders_ahead=orders_ahead,
            size_ahead=round(size_ahead, 4),
            total_level_size=round(queue.total_size, 4),
            total_level_orders=queue.order_count,
            queue_rank=rank,
        )

    def project_l2(self, max_levels: Optional[int] = None) -> dict:
        """
        Project L3 resting orders into consolidated Level-2 (MBP) aggregated order book.
        """
        limit = max_levels if max_levels is not None else self.max_depth_levels

        sorted_bid_prices = sorted(self.bids.keys(), reverse=True)[:limit]
        sorted_ask_prices = sorted(self.asks.keys(), reverse=False)[:limit]

        l2_bids = []
        cum_bid_size = 0.0
        cum_bid_notional = 0.0
        for p in sorted_bid_prices:
            q = self.bids[p]
            sz = round(q.total_size, 4)
            cum_bid_size += sz
            cum_bid_notional += p * sz
            l2_bids.append({
                "price": p,
                "size": sz,
                "order_count": q.order_count,
                "venues": {v: round(s, 4) for v, s in q.venue_sizes.items()},
                "cumulative_size": round(cum_bid_size, 4),
                "cumulative_notional": round(cum_bid_notional, 2),
            })

        l2_asks = []
        cum_ask_size = 0.0
        cum_ask_notional = 0.0
        for p in sorted_ask_prices:
            q = self.asks[p]
            sz = round(q.total_size, 4)
            cum_ask_size += sz
            cum_ask_notional += p * sz
            l2_asks.append({
                "price": p,
                "size": sz,
                "order_count": q.order_count,
                "venues": {v: round(s, 4) for v, s in q.venue_sizes.items()},
                "cumulative_size": round(cum_ask_size, 4),
                "cumulative_notional": round(cum_ask_notional, 2),
            })

        best_bid = sorted_bid_prices[0] if sorted_bid_prices else 0.0
        best_ask = sorted_ask_prices[0] if sorted_ask_prices else 0.0
        best_bid_sz = self.bids[best_bid].total_size if best_bid > 0 else 0.0
        best_ask_sz = self.asks[best_ask].total_size if best_ask > 0 else 0.0

        # Micro-price calculation: (P_ask * Q_bid + P_bid * Q_ask) / (Q_bid + Q_ask)
        tot_tob_size = best_bid_sz + best_ask_sz
        if tot_tob_size > 0:
            micro_price = (best_ask * best_bid_sz + best_bid * best_ask_sz) / tot_tob_size
            imbalance = (best_bid_sz - best_ask_sz) / tot_tob_size
        else:
            micro_price = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
            imbalance = 0.0

        is_crossed = (best_bid > best_ask) if (best_bid > 0 and best_ask > 0) else False

        return {
            "instrument": self.instrument_id,
            "bids": l2_bids,
            "asks": l2_asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": round(best_ask - best_bid, 4) if (best_bid > 0 and best_ask > 0) else 0.0,
            "micro_price": round(micro_price, 4),
            "imbalance_ratio": round(imbalance, 4),
            "is_crossed": is_crossed,
            "total_orders": len(self.orders),
            "total_bid_notional": round(cum_bid_notional, 2),
            "total_ask_notional": round(cum_ask_notional, 2),
        }

    def stats(self) -> dict:
        return {
            "instrument": self.instrument_id,
            "active_orders": len(self.orders),
            "bid_levels": len(self.bids),
            "ask_levels": len(self.asks),
            "total_adds": self._total_adds,
            "total_cancels": self._total_cancels,
            "total_modifies": self._total_modifies,
            "total_executes": self._total_executes,
        }
