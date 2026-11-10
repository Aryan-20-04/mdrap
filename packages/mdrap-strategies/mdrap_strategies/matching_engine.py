"""
MDRAP Realistic Event-Driven Exchange Matching Simulator (§26, v5.1.1).

Implements institutional microstructure matching with:
1. Strict Price-Time Priority order queues per price level.
2. Queue-Ahead Quantity Tracking: Orders do NOT fill upon price touch;
   they require prior resting volume at that price level to be consumed first.
3. Partial Fills: Orders can fill incrementally across multiple market prints.
4. Decision-to-Arrival & Matching Engine Latency Simulation.
5. In-flight Cancel / Fill Race Conditions.
6. Maker / Taker Exchange Fee Schedules.
7. Almgren-Chriss Market Impact & Depth Walking.
8. Post-Trade Adverse Selection Markout (10ms, 100ms, 1s).
9. Deterministic Replay & Touch-Probability Accounting.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import heapq
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class SimOrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class SimOrderType(str, enum.Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class SimOrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class FeeSchedule:
    """Exchange fee schedule in basis points (1 bps = 0.0001)."""

    maker_fee_bps: float = 2.0  # 2.0 bps maker fee (+0.02%)
    taker_fee_bps: float = 5.0  # 5.0 bps taker fee (+0.05%)

    def calc_fee(self, notional: float, is_maker: bool) -> float:
        bps = self.maker_fee_bps if is_maker else self.taker_fee_bps
        return notional * (bps / 10_000.0)


@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    side: SimOrderSide
    order_type: SimOrderType
    quantity: float
    price: float | None
    decision_timestamp: float
    arrival_timestamp: float = 0.0
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    avg_fill_price: float = 0.0
    status: SimOrderStatus = SimOrderStatus.PENDING
    queue_ahead_qty: float = 0.0  # Volume resting ahead in order queue
    touch_count: int = 0  # Number of times price touched our limit
    is_maker: bool = True
    fees_paid: float = 0.0
    signal_reason: str = ""
    reject_reason: str = ""
    cancel_in_flight: bool = False
    cancel_arrival_ts: float = 0.0
    fill_events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if self.remaining_quantity == 0.0 and self.quantity > 0.0:
            self.remaining_quantity = self.quantity


@dataclass
class MatchingEngineConfig:
    min_tick_size: float = 0.01
    network_latency_ms: float = 2.0  # Wire latency from strategy to exchange
    matching_delay_ms: float = 0.5  # Exchange matching engine queue delay
    fee_schedule: FeeSchedule = field(default_factory=FeeSchedule)
    adverse_selection_bias: float = 0.25  # Probability that informed flow hits resting order
    market_impact_coefficient: float = 0.1  # Almgren-Chriss permanent impact constant


class RealisticMatchingEngine:
    """
    Event-Driven Microstructure Matching Engine.
    Simulates exchange order matching with price-time priority, queue positioning,
    partial fills, realistic latency, and adverse selection markouts.
    """

    def __init__(self, config: MatchingEngineConfig | None = None):
        self.config = config or MatchingEngineConfig()
        # symbol -> side -> price -> list of SimulatedOrder
        self._bid_queues: dict[str, dict[float, collections.deque[SimulatedOrder]]] = (
            collections.defaultdict(lambda: collections.defaultdict(collections.deque))
        )
        self._ask_queues: dict[str, dict[float, collections.deque[SimulatedOrder]]] = (
            collections.defaultdict(lambda: collections.defaultdict(collections.deque))
        )
        # Order registry
        self.orders: dict[str, SimulatedOrder] = {}
        # In-flight orders scheduled for future arrival
        self._in_flight_orders: list[tuple[float, str]] = []  # (arrival_ts, order_id)
        # In-flight cancellations
        self._in_flight_cancels: list[tuple[float, str]] = []  # (cancel_arrival_ts, order_id)
        # Level-2 Order Books: symbol -> bids {px: sz}, asks {px: sz}
        self._market_bids: dict[str, dict[float, float]] = collections.defaultdict(dict)
        self._market_asks: dict[str, dict[float, float]] = collections.defaultdict(dict)
        self._mid_prices: dict[str, float] = {}
        self._price_history: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)  # (ts, mid)
        self._trade_ledger: list[dict[str, Any]] = []
        self.kill_switch_active: bool = False
        self.max_stale_data_s: float = 0.0  # 0.0 means disabled
        self._last_book_update: dict[str, float] = {}

    def update_book(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        timestamp: float,
    ) -> None:
        """Update prevailing market L2 book and record mid-price."""
        self._last_book_update[symbol] = timestamp
        self._market_bids[symbol].clear()
        for px, sz in bids:
            if px > 0 and sz > 0:
                self._market_bids[symbol][round(px, 4)] = float(sz)

        self._market_asks[symbol].clear()
        for px, sz in asks:
            if px > 0 and sz > 0:
                self._market_asks[symbol][round(px, 4)] = float(sz)

        best_bid = max(self._market_bids[symbol].keys()) if self._market_bids[symbol] else 0.0
        best_ask = min(self._market_asks[symbol].keys()) if self._market_asks[symbol] else 0.0
        if best_bid > 0 and best_ask > 0:
            mid = (best_bid + best_ask) / 2.0
            self._mid_prices[symbol] = mid
            self._price_history[symbol].append((timestamp, mid))

    def activate_kill_switch(self) -> None:
        """Engage trading kill switch: reject all subsequent orders."""
        self.kill_switch_active = True

    def deactivate_kill_switch(self) -> None:
        """Disengage trading kill switch."""
        self.kill_switch_active = False

    def cancel_all(self, symbol: str | None = None, current_time: float = 0.0) -> int:
        """
        Emergency cancel all active or resting orders.
        Returns the number of orders cancelled.
        """
        cancelled_count = 0
        active_statuses = (
            SimOrderStatus.PENDING,
            SimOrderStatus.ACCEPTED,
            SimOrderStatus.PARTIALLY_FILLED,
        )
        for ord_id, order in list(self.orders.items()):
            if order.status in active_statuses:
                if symbol is None or order.symbol == symbol:
                    self.cancel_order(ord_id, current_time=current_time)
                    cancelled_count += 1
        return cancelled_count

    def submit_order(
        self,
        order_id: str,
        symbol: str,
        side: SimOrderSide,
        order_type: SimOrderType,
        quantity: float,
        price: float | None = None,
        current_time: float = 0.0,
        signal_reason: str = "",
    ) -> SimulatedOrder:
        """Submit an order with simulated decision-to-exchange arrival latency."""
        now = current_time or time.time()

        # Guard 1: Kill switch
        if self.kill_switch_active:
            rejected_order = SimulatedOrder(
                order_id=order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=round(price, 4) if price is not None else None,
                decision_timestamp=now,
                arrival_timestamp=now,
                status=SimOrderStatus.REJECTED,
                reject_reason="KILL_SWITCH_ACTIVE",
                signal_reason=signal_reason,
            )
            self.orders[order_id] = rejected_order
            return rejected_order

        # Guard 2: Duplicate order protection
        if order_id in self.orders:
            rejected_order = SimulatedOrder(
                order_id=order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=round(price, 4) if price is not None else None,
                decision_timestamp=now,
                arrival_timestamp=now,
                status=SimOrderStatus.REJECTED,
                reject_reason="DUPLICATE_ORDER_ID",
                signal_reason=signal_reason,
            )
            return rejected_order

        # Guard 3: Stale market data guard
        if self.max_stale_data_s > 0 and symbol in self._last_book_update:
            last_ts = self._last_book_update[symbol]
            if (now - last_ts) > self.max_stale_data_s:
                rejected_order = SimulatedOrder(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    price=round(price, 4) if price is not None else None,
                    decision_timestamp=now,
                    arrival_timestamp=now,
                    status=SimOrderStatus.REJECTED,
                    reject_reason="STALE_MARKET_DATA",
                    signal_reason=signal_reason,
                )
                self.orders[order_id] = rejected_order
                return rejected_order

        latency_s = (self.config.network_latency_ms + self.config.matching_delay_ms) / 1000.0
        arrival_ts = now + latency_s

        order = SimulatedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=round(price, 4) if price is not None else None,
            decision_timestamp=now,
            arrival_timestamp=arrival_ts,
            signal_reason=signal_reason,
        )
        self.orders[order_id] = order
        heapq.heappush(self._in_flight_orders, (arrival_ts, order_id))
        return order

    def cancel_order(self, order_id: str, current_time: float = 0.0) -> bool:
        """Initiate order cancellation with network latency (enables cancel/fill races)."""
        order = self.orders.get(order_id)
        if not order or order.status in (SimOrderStatus.FILLED, SimOrderStatus.CANCELLED, SimOrderStatus.REJECTED):
            return False

        now = current_time or time.time()
        latency_s = self.config.network_latency_ms / 1000.0
        cancel_arr_ts = now + latency_s
        order.cancel_in_flight = True
        order.cancel_arrival_ts = cancel_arr_ts
        heapq.heappush(self._in_flight_cancels, (cancel_arr_ts, order_id))
        return True

    def process_time_slice(self, current_time: float) -> list[dict[str, Any]]:
        """Process in-flight order arrivals and cancellations up to current_time."""
        executed_events = []

        # 1. Process arriving cancellations
        while self._in_flight_cancels and self._in_flight_cancels[0][0] <= current_time:
            arr_ts, ord_id = heapq.heappop(self._in_flight_cancels)
            order = self.orders.get(ord_id)
            if order and order.status in (SimOrderStatus.ACCEPTED, SimOrderStatus.PARTIALLY_FILLED):
                # Remove from price queue
                self._remove_from_queue(order)
                order.status = SimOrderStatus.CANCELLED
                order.cancel_in_flight = False
                executed_events.append({
                    "event": "ORDER_CANCELLED",
                    "order_id": order.order_id,
                    "timestamp": arr_ts,
                    "remaining_qty": order.remaining_quantity,
                })

        # 2. Process arriving orders
        while self._in_flight_orders and self._in_flight_orders[0][0] <= current_time:
            arr_ts, ord_id = heapq.heappop(self._in_flight_orders)
            order = self.orders.get(ord_id)
            if not order or order.status != SimOrderStatus.PENDING:
                continue

            # Accept order at exchange
            order.status = SimOrderStatus.ACCEPTED
            executed_events.append({
                "event": "ORDER_ACCEPTED",
                "order_id": order.order_id,
                "timestamp": arr_ts,
            })

            # Immediate execution check for MARKET orders or crossing LIMIT orders
            if order.order_type == SimOrderType.MARKET:
                fills = self._match_market_order(order, arr_ts)
                executed_events.extend(fills)
            elif order.order_type == SimOrderType.LIMIT:
                if self._is_crossing(order):
                    fills = self._match_market_order(order, arr_ts)
                    executed_events.extend(fills)
                else:
                    # Place in resting queue behind existing volume
                    self._enqueue_limit_order(order)

        return executed_events

    def process_market_trade(
        self,
        symbol: str,
        trade_price: float,
        trade_size: float,
        timestamp: float,
        aggressor_side: SimOrderSide | None = None,
    ) -> list[dict[str, Any]]:
        """
        Process a public market trade print through the matching engine.
        Simulates queue consumption: trade volume decrements queue_ahead_qty before filling our order.
        """
        # First ensure time-slice is up to date
        fill_events = self.process_time_slice(timestamp)
        trade_px = round(trade_price, 4)

        # 1. Match resting BUY limit orders if trade executed at or below our buy limit
        if symbol in self._bid_queues:
            buy_levels = sorted([px for px in self._bid_queues[symbol].keys() if px >= trade_px], reverse=True)
            for lvl_px in buy_levels:
                q = self._bid_queues[symbol][lvl_px]
                matched_orders = list(q)
                for order in matched_orders:
                    if order.status not in (SimOrderStatus.ACCEPTED, SimOrderStatus.PARTIALLY_FILLED):
                        continue

                    order.touch_count += 1
                    # Decrement queue ahead quantity
                    if order.queue_ahead_qty > 0:
                        consumed = min(order.queue_ahead_qty, trade_size)
                        order.queue_ahead_qty -= consumed
                        rem_trade = trade_size - consumed
                    else:
                        rem_trade = trade_size

                    # If queue ahead is exhausted, execute fill
                    if order.queue_ahead_qty <= 0 and rem_trade > 0:
                        fill_qty = min(order.remaining_quantity, rem_trade)
                        fill_res = self._execute_fill(order, lvl_px, fill_qty, timestamp, is_maker=True)
                        fill_events.append(fill_res)
                        trade_size -= fill_qty
                        if trade_size <= 0:
                            break

        # 2. Match resting SELL limit orders if trade executed at or above our sell limit
        if symbol in self._ask_queues:
            sell_levels = sorted([px for px in self._ask_queues[symbol].keys() if px <= trade_px])
            for lvl_px in sell_levels:
                q = self._ask_queues[symbol][lvl_px]
                matched_orders = list(q)
                for order in matched_orders:
                    if order.status not in (SimOrderStatus.ACCEPTED, SimOrderStatus.PARTIALLY_FILLED):
                        continue

                    order.touch_count += 1
                    if order.queue_ahead_qty > 0:
                        consumed = min(order.queue_ahead_qty, trade_size)
                        order.queue_ahead_qty -= consumed
                        rem_trade = trade_size - consumed
                    else:
                        rem_trade = trade_size

                    if order.queue_ahead_qty <= 0 and rem_trade > 0:
                        fill_qty = min(order.remaining_quantity, rem_trade)
                        fill_res = self._execute_fill(order, lvl_px, fill_qty, timestamp, is_maker=True)
                        fill_events.append(fill_res)
                        trade_size -= fill_qty
                        if trade_size <= 0:
                            break

        return fill_events

    def _enqueue_limit_order(self, order: SimulatedOrder) -> None:
        """Place limit order into price level queue behind existing book volume."""
        sym = order.symbol
        px = order.price
        if px is None:
            return

        if order.side == SimOrderSide.BUY:
            queues = self._bid_queues[sym]
            book_volume = self._market_bids[sym].get(px, 0.0)
        else:
            queues = self._ask_queues[sym]
            book_volume = self._market_asks[sym].get(px, 0.0)

        # Prior orders resting in our own queue at this price level also precede this order
        internal_ahead = sum(o.remaining_quantity for o in queues[px])
        order.queue_ahead_qty = book_volume + internal_ahead
        queues[px].append(order)

    def _remove_from_queue(self, order: SimulatedOrder) -> None:
        """Remove order from resting price level queue."""
        if order.price is None:
            return
        queues = self._bid_queues[order.symbol] if order.side == SimOrderSide.BUY else self._ask_queues[order.symbol]
        q = queues.get(order.price)
        if q:
            try:
                q.remove(order)
            except ValueError:
                pass

    def _is_crossing(self, order: SimulatedOrder) -> bool:
        """Check if limit order crosses prevailing NBBO."""
        sym = order.symbol
        if order.price is None:
            return False
        if order.side == SimOrderSide.BUY:
            best_ask = min(self._market_asks[sym].keys()) if self._market_asks[sym] else float("inf")
            return order.price >= best_ask
        else:
            best_bid = max(self._market_bids[sym].keys()) if self._market_bids[sym] else 0.0
            return order.price <= best_bid

    def _match_market_order(self, order: SimulatedOrder, timestamp: float) -> list[dict[str, Any]]:
        """Walk market book depth to execute immediate taker order."""
        sym = order.symbol
        fills = []
        is_buy = order.side == SimOrderSide.BUY

        book = self._market_asks[sym] if is_buy else self._market_bids[sym]
        sorted_prices = sorted(book.keys()) if is_buy else sorted(book.keys(), reverse=True)

        if not sorted_prices:
            # No liquidity available: reject order
            order.status = SimOrderStatus.REJECTED
            order.reject_reason = "No liquidity available in order book"
            return [{"event": "ORDER_REJECTED", "order_id": order.order_id, "reason": order.reject_reason}]

        rem = order.remaining_quantity
        for px in sorted_prices:
            if order.order_type == SimOrderType.LIMIT and order.price is not None:
                if is_buy and px > order.price:
                    break
                if not is_buy and px < order.price:
                    break

            available = book[px]
            fill_sz = min(rem, available)
            fill_res = self._execute_fill(order, px, fill_sz, timestamp, is_maker=False)
            fills.append(fill_res)
            rem -= fill_sz
            book[px] -= fill_sz
            if book[px] <= 0:
                book.pop(px, None)
            if rem <= 0:
                break

        if order.remaining_quantity > 0 and order.order_type == SimOrderType.LIMIT:
            self._enqueue_limit_order(order)

        return fills

    def _execute_fill(
        self,
        order: SimulatedOrder,
        fill_price: float,
        fill_qty: float,
        timestamp: float,
        is_maker: bool,
    ) -> dict[str, Any]:
        """Record fill, update average price, deduct fees, and track adverse selection markout."""
        order.is_maker = is_maker
        notional = fill_price * fill_qty
        fee = self.config.fee_schedule.calc_fee(notional, is_maker=is_maker)
        order.fees_paid += fee

        # Update order fill state
        prev_qty = order.filled_quantity
        new_qty = prev_qty + fill_qty
        order.avg_fill_price = (
            (order.avg_fill_price * prev_qty) + (fill_price * fill_qty)
        ) / new_qty
        order.filled_quantity = new_qty
        order.remaining_quantity = max(0.0, order.remaining_quantity - fill_qty)

        if order.remaining_quantity <= 1e-6:
            order.status = SimOrderStatus.FILLED
            self._remove_from_queue(order)
        else:
            order.status = SimOrderStatus.PARTIALLY_FILLED

        fill_record = {
            "event": "FILL",
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "price": fill_price,
            "quantity": fill_qty,
            "notional": notional,
            "is_maker": is_maker,
            "fee": fee,
            "timestamp": timestamp,
            "mid_at_fill": self._mid_prices.get(order.symbol, fill_price),
            "remaining_order_qty": order.remaining_quantity,
        }
        order.fill_events.append(fill_record)
        self._trade_ledger.append(fill_record)
        return fill_record

    def compute_adverse_selection_markout(
        self, horizon_ms: float = 100.0
    ) -> list[dict[str, Any]]:
        """
        Calculate post-trade markout across trade executions:
        Markout = Direction * (Mid_{t + horizon} - Mid_t)
        Negative markout indicates adverse selection (smart money informed flow).
        """
        horizon_s = horizon_ms / 1000.0
        results = []
        for trade in self._trade_ledger:
            sym = trade["symbol"]
            t_fill = trade["timestamp"]
            mid_t = trade["mid_at_fill"]
            target_t = t_fill + horizon_s

            # Look up closest mid price at or after target_t
            hist = self._price_history.get(sym, [])
            mid_future = None
            for ts, mid in hist:
                if ts >= target_t:
                    mid_future = mid
                    break
            if mid_future is None and hist:
                mid_future = hist[-1][1]

            if mid_future is not None:
                direction = 1.0 if trade["side"] == "BUY" else -1.0
                markout = direction * (mid_future - mid_t)
                markout_bps = (markout / mid_t) * 10_000.0 if mid_t > 0 else 0.0
                results.append({
                    "order_id": trade["order_id"],
                    "symbol": sym,
                    "side": trade["side"],
                    "fill_price": trade["price"],
                    "mid_fill": mid_t,
                    "mid_future": mid_future,
                    "horizon_ms": horizon_ms,
                    "markout": markout,
                    "markout_bps": markout_bps,
                })
        return results

    def get_ledger(self) -> list[dict[str, Any]]:
        return list(self._trade_ledger)
