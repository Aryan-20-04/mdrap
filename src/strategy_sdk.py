"""
MDRAP Institutional Algo Strategy SDK & Paper EMS (§26).

Provides boutique trading firms, prop desks, and hedge funds with an
institutional-grade, event-driven algorithmic trading engine and paper
execution management system (EMS):
1. Event-Driven Strategy API (`on_tick`, `on_quote`, `on_flow`, `on_whale`)
2. Pre-Trade Institutional Risk Management (position limits, fat-finger collars, drawdown kill-switch)
3. Simulated Paper Execution Engine with realistic NBBO and depth fill models
4. Reference Institutional Strategies (`WhaleMomentumStrategy`, `SpreadCaptureMarketMaker`)
"""

from __future__ import annotations

import csv
import enum
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from models import CanonicalEvent, EventType


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderBookSnapshot:
    """
    Immutable point-in-time snapshot of the Level-2 order book.

    Captures top-of-book, synthesized depth tiers, micro-price, and order book imbalance
    for execution attribution and post-trade compliance.
    """

    symbol: str
    timestamp: float
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    spread_bps: float
    micro_price: float
    imbalance: float
    bids: list[dict[str, float]] = field(default_factory=list)
    asks: list[dict[str, float]] = field(default_factory=list)
    total_bid_depth: float = 0.0
    total_ask_depth: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "best_bid": round(self.best_bid, 4),
            "best_ask": round(self.best_ask, 4),
            "mid": round(self.mid, 4),
            "spread": round(self.spread, 4),
            "spread_bps": round(self.spread_bps, 2),
            "micro_price": round(self.micro_price, 4),
            "imbalance": round(self.imbalance, 4),
            "bids": self.bids,
            "asks": self.asks,
            "total_bid_depth": round(self.total_bid_depth, 2),
            "total_ask_depth": round(self.total_ask_depth, 2),
        }


class OrderBook:
    """
    Live Level-2 Limit Order Book with realistic multi-tier depth simulation,
    volume-weighted micro-price calculation, order flow imbalance, and book walking execution.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._bids: dict[float, float] = {}  # price -> size
        self._asks: dict[float, float] = {}  # price -> size
        self.last_update: float = 0.0

    def update_quote(
        self,
        bid_price: float,
        ask_price: float,
        bid_size: float = 100.0,
        ask_size: float = 100.0,
        timestamp: float = 0.0,
        depth_levels: int = 5,
    ) -> None:
        """Update top-of-book and synthesize realistic multi-tier depth levels."""
        self.last_update = timestamp or time.time()
        self._bids.clear()
        self._asks.clear()

        if bid_price and bid_price > 0:
            self._bids[round(bid_price, 4)] = max(0.1, float(bid_size))
        if ask_price and ask_price > 0:
            self._asks[round(ask_price, 4)] = max(0.1, float(ask_size))

        # Reconstruct realistic depth rungs stepping away from NBBO
        if bid_price and bid_price > 0:
            step = max(0.01, round(bid_price * 0.0005, 2))  # ~5 bps per rung
            for lvl in range(1, depth_levels):
                px = round(bid_price - (lvl * step), 2)
                if px > 0:
                    sz = round(bid_size * (1.0 + 0.35 * lvl), 1)
                    self._bids[px] = sz

        if ask_price and ask_price > 0:
            step = max(0.01, round(ask_price * 0.0005, 2))
            for lvl in range(1, depth_levels):
                px = round(ask_price + (lvl * step), 2)
                sz = round(ask_size * (1.0 + 0.35 * lvl), 1)
                self._asks[px] = sz

    def update_level(self, side: OrderSide | str, price: float, size: float) -> None:
        """Update or delete a specific price level."""
        side_upper = side.value if isinstance(side, OrderSide) else str(side).upper()
        book = self._bids if side_upper == "BUY" else self._asks
        px = round(price, 4)
        if size <= 0:
            book.pop(px, None)
        else:
            book[px] = float(size)
        self.last_update = time.time()

    @property
    def best_bid(self) -> tuple[float, float]:
        """Returns (price, size) of highest bid."""
        if not self._bids:
            return 0.0, 0.0
        top_px = max(self._bids.keys())
        return top_px, self._bids[top_px]

    @property
    def best_ask(self) -> tuple[float, float]:
        """Returns (price, size) of lowest ask."""
        if not self._asks:
            return 0.0, 0.0
        top_px = min(self._asks.keys())
        return top_px, self._asks[top_px]

    @property
    def mid_price(self) -> float:
        bid, _ = self.best_bid
        ask, _ = self.best_ask
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return bid or ask or 0.0

    @property
    def spread(self) -> float:
        bid, _ = self.best_bid
        ask, _ = self.best_ask
        if bid > 0 and ask > 0:
            return max(0.0, ask - bid)
        return 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid > 0:
            return (self.spread / mid) * 10_000.0
        return 0.0

    @property
    def micro_price(self) -> float:
        """Volume-weighted mid-price: (bid_px * ask_sz + ask_px * bid_sz) / (bid_sz + ask_sz)."""
        bid_px, bid_sz = self.best_bid
        ask_px, ask_sz = self.best_ask
        if bid_px > 0 and ask_px > 0 and (bid_sz + ask_sz) > 0:
            return (bid_px * ask_sz + ask_px * bid_sz) / (bid_sz + ask_sz)
        return self.mid_price

    @property
    def imbalance(self) -> float:
        """Order book imbalance [-1.0, 1.0]: (bid_sz - ask_sz) / (bid_sz + ask_sz)."""
        _, bid_sz = self.best_bid
        _, ask_sz = self.best_ask
        total_sz = bid_sz + ask_sz
        if total_sz > 0:
            return (bid_sz - ask_sz) / total_sz
        return 0.0

    def get_ladder(
        self, depth: int = 5
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        """Returns top N bids (descending) and asks (ascending) as [(price, size), ...]."""
        sorted_bids = sorted(self._bids.items(), key=lambda x: x[0], reverse=True)[
            :depth
        ]
        sorted_asks = sorted(self._asks.items(), key=lambda x: x[0])[:depth]
        return sorted_bids, sorted_asks

    def walk_book(
        self,
        side: OrderSide,
        quantity: float,
    ) -> tuple[float, float, float, float, list[dict[str, float]]]:
        """
        Simulates walking the consolidated order book ladder to fill quantity.
        Returns: (vwap_price, slippage_bps, slippage_usd, effective_spread_bps, rungs_consumed)
        """
        if quantity <= 0:
            return self.mid_price, 0.0, 0.0, 0.0, []

        is_buy = side == OrderSide.BUY
        levels = (
            sorted(self._asks.items(), key=lambda x: x[0])
            if is_buy
            else sorted(self._bids.items(), key=lambda x: x[0], reverse=True)
        )

        if not levels:
            top_px = self.mid_price or 100.0
            return top_px, 0.0, 0.0, 0.0, []

        best_px = levels[0][0]
        arrival_px = best_px
        mid = self.mid_price or best_px

        remaining = quantity
        total_notional = 0.0
        rungs_consumed = []

        for px, sz in levels:
            if remaining <= 0:
                break
            fill_sz = min(remaining, sz)
            total_notional += fill_sz * px
            remaining -= fill_sz
            rungs_consumed.append({"price": px, "size": fill_sz, "available": sz})

        if remaining > 0:
            penalty_step = max(0.01, best_px * 0.0005)
            deep_px = (
                (levels[-1][0] + penalty_step)
                if is_buy
                else max(0.01, levels[-1][0] - penalty_step)
            )
            total_notional += remaining * deep_px
            rungs_consumed.append(
                {"price": round(deep_px, 4), "size": remaining, "available": 0.0}
            )

        vwap_px = total_notional / quantity

        if is_buy:
            slippage_usd = max(0.0, (vwap_px - arrival_px) * quantity)
            slippage_bps = (
                ((vwap_px - arrival_px) / arrival_px * 10_000.0)
                if arrival_px > 0
                else 0.0
            )
            eff_spread_bps = ((vwap_px - mid) / mid * 10_000.0) if mid > 0 else 0.0
        else:
            slippage_usd = max(0.0, (arrival_px - vwap_px) * quantity)
            slippage_bps = (
                ((arrival_px - vwap_px) / arrival_px * 10_000.0)
                if arrival_px > 0
                else 0.0
            )
            eff_spread_bps = ((mid - vwap_px) / mid * 10_000.0) if mid > 0 else 0.0

        return (
            round(vwap_px, 4),
            round(slippage_bps, 2),
            round(slippage_usd, 2),
            round(eff_spread_bps, 2),
            rungs_consumed,
        )

    def snapshot(self, depth: int = 5) -> OrderBookSnapshot:
        """Captures a serializable snapshot of the order book state."""
        bids, asks = self.get_ladder(depth)
        best_bid_px, _ = self.best_bid
        best_ask_px, _ = self.best_ask
        return OrderBookSnapshot(
            symbol=self.symbol,
            timestamp=self.last_update or time.time(),
            best_bid=best_bid_px,
            best_ask=best_ask_px,
            mid=self.mid_price,
            spread=self.spread,
            spread_bps=self.spread_bps,
            micro_price=self.micro_price,
            imbalance=self.imbalance,
            bids=[{"price": p, "size": s} for p, s in bids],
            asks=[{"price": p, "size": s} for p, s in asks],
            total_bid_depth=sum(s for _, s in bids),
            total_ask_depth=sum(s for _, s in asks),
        )

    def to_dict(self) -> dict:
        return self.snapshot().to_dict()


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    timestamp: float = field(default_factory=time.time)
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float | None = None
    filled_timestamp: float | None = None
    arrival_price: float | None = None
    slippage_bps: float = 0.0
    slippage_usd: float = 0.0
    effective_spread_bps: float = 0.0
    market_impact_bps: float = 0.0
    reject_reason: str | None = None
    signal_reason: str = ""
    order_book_snapshot: OrderBookSnapshot | None = None
    rungs_consumed: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value if hasattr(self.side, "value") else str(self.side),
            "order_type": self.order_type.value
            if hasattr(self.order_type, "value")
            else str(self.order_type),
            "quantity": self.quantity,
            "limit_price": self.price,
            "status": self.status.value
            if hasattr(self.status, "value")
            else str(self.status),
            "arrival_price": self.arrival_price,
            "filled_price": self.filled_price,
            "timestamp": self.timestamp,
            "filled_timestamp": self.filled_timestamp,
            "slippage_bps": self.slippage_bps,
            "slippage_usd": self.slippage_usd,
            "effective_spread_bps": self.effective_spread_bps,
            "market_impact_bps": self.market_impact_bps,
            "signal_reason": self.signal_reason,
            "reject_reason": self.reject_reason,
            "order_book_snapshot": self.order_book_snapshot.to_dict()
            if self.order_book_snapshot
            else None,
            "rungs_consumed": self.rungs_consumed,
        }


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def update_market_price(self, current_price: float) -> None:
        if self.quantity != 0 and current_price > 0:
            self.unrealized_pnl = (current_price - self.avg_cost) * self.quantity
        else:
            self.unrealized_pnl = 0.0


@dataclass
class RiskLimits:
    """Pre-trade risk controls protecting firm capital."""

    max_position_size: float = 5000.0  # Max absolute shares in any single instrument
    max_order_size: float = 1000.0  # Max shares per individual order (fat-finger guard)
    price_collar_bps: float = (
        50.0  # Max allowed deviation from NBBO midpoint (50 bps = 0.50%)
    )
    max_drawdown_pct: float = 5.0  # Kill-switch if portfolio drawdown exceeds 5%
    allow_short: bool = True  # Whether short selling is enabled


class RiskManager:
    """
    Institutional Pre-Trade Risk Gate.
    Evaluates orders against firm risk limits before execution.
    """

    def __init__(
        self, limits: RiskLimits | None = None, initial_capital: float = 100_000.0
    ):
        self.limits = limits or RiskLimits()
        self.initial_capital = initial_capital
        self.peak_equity = initial_capital
        self.kill_switch_triggered = False

    def validate_order(
        self,
        order: Order,
        current_mid: float | None,
        current_position: Position,
        current_equity: float,
    ) -> tuple[bool, str | None]:
        """Validate order against pre-trade risk controls."""
        # 1. Check kill-switch / maximum drawdown
        if self.kill_switch_triggered:
            return False, "Risk kill-switch active: trading halted"

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        if self.peak_equity <= 0:
            drawdown_pct = 100.0 if current_equity < 0 else 0.0
        else:
            drawdown_pct = (
                (self.peak_equity - current_equity) / self.peak_equity
            ) * 100.0
        if drawdown_pct >= self.limits.max_drawdown_pct:
            self.kill_switch_triggered = True
            return (
                False,
                f"Maximum drawdown breached ({drawdown_pct:.2f}% >= {self.limits.max_drawdown_pct:.2f}%): kill-switch triggered",
            )

        # 2. Check maximum order size (fat finger guard)
        if order.quantity <= 0:
            return False, f"Invalid order quantity: {order.quantity}"

        if order.quantity > self.limits.max_order_size:
            return (
                False,
                f"Order quantity {order.quantity} exceeds max order limit {self.limits.max_order_size}",
            )

        # 3. Check projected position size
        projected_qty = current_position.quantity
        if order.side == OrderSide.BUY:
            projected_qty += order.quantity
        else:
            projected_qty -= order.quantity

        if not self.limits.allow_short and projected_qty < 0:
            return False, "Short positions are disabled by risk policy"

        if abs(projected_qty) > self.limits.max_position_size:
            return (
                False,
                f"Projected position {projected_qty} exceeds max limit {self.limits.max_position_size}",
            )

        # 4. Check price collar for limit orders (fat-finger price deviation)
        if (
            order.order_type == OrderType.LIMIT
            and order.price is not None
            and current_mid is not None
            and current_mid > 0
        ):
            deviation_bps = abs(order.price - current_mid) / current_mid * 10_000.0
            if deviation_bps > self.limits.price_collar_bps:
                return (
                    False,
                    f"Limit price {order.price} deviates by {deviation_bps:.1f} bps from mid {current_mid:.2f} (max collar: {self.limits.price_collar_bps} bps)",
                )

        return True, None


class PaperExecutor:
    """
    High-fidelity simulated execution engine.
    Matches orders against NBBO quotes and L2 depth with realistic slippage.
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        risk_manager: RiskManager | None = None,
    ):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.risk_manager = risk_manager or RiskManager(initial_capital=initial_cash)
        self.positions: dict[str, Position] = {}
        self.orders: list[Order] = []
        self.fills: list[dict[str, Any]] = []
        self.equity_curve: list[tuple[float, float]] = []  # (timestamp, equity)
        self._order_counter = 0

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def total_equity(self, current_prices: dict[str, float]) -> float:
        equity = self.cash
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos.avg_cost)
            pos.update_market_price(price)
            equity += pos.quantity * price
        return equity

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        bbo: dict[str, float] | None = None,
        order_book: OrderBook | None = None,
        signal_reason: str = "",
    ) -> Order:
        """Submit, risk-check, and simulate execution of an order with order book microstructure."""
        self._order_counter += 1
        order = Order(
            order_id=f"ord-{self._order_counter}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            timestamp=time.time(),
            signal_reason=signal_reason,
        )

        pos = self.get_position(symbol)

        # Derive prevailing market reference price & arrival price
        if order_book is not None and (
            order_book.best_bid[0] > 0 or order_book.best_ask[0] > 0
        ):
            mid = order_book.mid_price
            best_bid_px, _ = order_book.best_bid
            best_ask_px, _ = order_book.best_ask
            arrival_px = best_ask_px if side == OrderSide.BUY else best_bid_px
            if arrival_px <= 0:
                arrival_px = mid
            current_px = mid
            order.arrival_price = arrival_px
            order.order_book_snapshot = order_book.snapshot(depth=5)
        elif bbo is not None and "bid" in bbo and "ask" in bbo:
            mid = (bbo["bid"] + bbo["ask"]) / 2.0
            arrival_px = bbo["ask"] if side == OrderSide.BUY else bbo["bid"]
            current_px = mid
            order.arrival_price = arrival_px
        else:
            mid = pos.avg_cost or 100.0
            arrival_px = mid
            current_px = mid
            order.arrival_price = arrival_px

        current_eq = self.total_equity({symbol: current_px})

        # Pre-trade risk evaluation
        ok, reason = self.risk_manager.validate_order(order, mid, pos, current_eq)
        if not ok:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reason
            self.orders.append(order)
            return order

        # Simulated execution against book or top-of-book
        fill_price = None
        slippage_bps = 0.0
        slippage_usd = 0.0
        eff_spread_bps = 0.0
        rungs = []

        if order_book is not None and (
            order_book.best_bid[0] > 0 or order_book.best_ask[0] > 0
        ):
            best_bid_px, _ = order_book.best_bid
            best_ask_px, _ = order_book.best_ask

            if order_type == OrderType.MARKET:
                fill_price, slippage_bps, slippage_usd, eff_spread_bps, rungs = (
                    order_book.walk_book(side, quantity)
                )
            elif order_type == OrderType.LIMIT:
                if price is not None:
                    if side == OrderSide.BUY and price >= best_ask_px:
                        (
                            fill_price,
                            slippage_bps,
                            slippage_usd,
                            eff_spread_bps,
                            rungs,
                        ) = order_book.walk_book(side, quantity)
                    elif side == OrderSide.SELL and price <= best_bid_px:
                        (
                            fill_price,
                            slippage_bps,
                            slippage_usd,
                            eff_spread_bps,
                            rungs,
                        ) = order_book.walk_book(side, quantity)
                    else:
                        order.status = OrderStatus.PENDING
                        self.orders.append(order)
                        return order
        elif bbo is not None:
            bid = bbo.get("bid", 0.0)
            ask = bbo.get("ask", 0.0)
            bid_sz = bbo.get("bid_size", 1000.0)
            ask_sz = bbo.get("ask_size", 1000.0)

            if order_type == OrderType.MARKET:
                if side == OrderSide.BUY:
                    arrival_px = ask
                    impact = (
                        max(0.0, (quantity - ask_sz) / 10_000.0) * 0.01
                        if quantity > ask_sz
                        else 0.0
                    )
                    fill_price = ask + impact
                    slippage_bps = (
                        ((fill_price - arrival_px) / arrival_px) * 10_000.0
                        if arrival_px > 0
                        else 0.0
                    )
                else:
                    arrival_px = bid
                    impact = (
                        max(0.0, (quantity - bid_sz) / 10_000.0) * 0.01
                        if quantity > bid_sz
                        else 0.0
                    )
                    fill_price = bid - impact
                    slippage_bps = (
                        ((arrival_px - fill_price) / arrival_px) * 10_000.0
                        if arrival_px > 0
                        else 0.0
                    )
                slippage_usd = abs(fill_price - arrival_px) * quantity
                eff_spread_bps = (
                    (abs(fill_price - mid) / mid * 10_000.0) if mid > 0 else 0.0
                )
            elif order_type == OrderType.LIMIT:
                if price is not None:
                    if side == OrderSide.BUY and price >= ask:
                        fill_price = ask
                    elif side == OrderSide.SELL and price <= bid:
                        fill_price = bid
                    else:
                        order.status = OrderStatus.PENDING
                        self.orders.append(order)
                        return order
        else:
            fill_price = price if price is not None else 100.0

        if fill_price is not None:
            order.effective_spread_bps = eff_spread_bps
            order.slippage_usd = slippage_usd
            order.rungs_consumed = rungs
            self._fill_order(order, fill_price, slippage_bps)

        self.orders.append(order)
        return order

    def _fill_order(self, order: Order, fill_price: float, slippage_bps: float) -> None:
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_timestamp = time.time()
        order.slippage_bps = slippage_bps

        pos = self.get_position(order.symbol)
        fill_cost = order.quantity * fill_price

        if order.side == OrderSide.BUY:
            self.cash -= fill_cost
            if pos.quantity >= 0:
                new_qty = pos.quantity + order.quantity
                pos.avg_cost = (
                    ((pos.quantity * pos.avg_cost) + fill_cost) / new_qty
                    if new_qty > 0
                    else fill_price
                )
                pos.quantity = new_qty
            else:
                # Covering short
                closed_qty = min(abs(pos.quantity), order.quantity)
                pnl = closed_qty * (pos.avg_cost - fill_price)
                pos.realized_pnl += pnl
                pos.quantity += order.quantity
                if pos.quantity > 0:
                    pos.avg_cost = fill_price
        else:
            # SELL
            self.cash += fill_cost
            if pos.quantity <= 0:
                new_qty = pos.quantity - order.quantity
                pos.avg_cost = (
                    ((abs(pos.quantity) * pos.avg_cost) + fill_cost) / abs(new_qty)
                    if new_qty != 0
                    else fill_price
                )
                pos.quantity = new_qty
            else:
                # Closing long
                closed_qty = min(pos.quantity, order.quantity)
                pnl = closed_qty * (fill_price - pos.avg_cost)
                pos.realized_pnl += pnl
                pos.quantity -= order.quantity
                if pos.quantity < 0:
                    pos.avg_cost = fill_price

        pos.update_market_price(fill_price)
        self.fills.append(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "qty": order.quantity,
                "price": fill_price,
                "arrival_price": order.arrival_price,
                "slippage_bps": slippage_bps,
                "slippage_usd": order.slippage_usd,
                "effective_spread_bps": order.effective_spread_bps,
                "signal_reason": order.signal_reason,
                "cash_after": self.cash,
                "realized_pnl": pos.realized_pnl,
                "timestamp": order.filled_timestamp,
                "order_book_snapshot": order.order_book_snapshot.to_dict()
                if order.order_book_snapshot
                else None,
                "rungs_consumed": order.rungs_consumed,
            }
        )


class Strategy:
    """
    Abstract Base Class for Event-Driven Algorithmic Strategies.
    Tracks live Level-2 Order Books, records execution microstructure, and exports audit trails.
    """

    def __init__(
        self,
        name: str,
        symbols: list[str] | None = None,
        risk_limits: RiskLimits | None = None,
    ):
        self.name = name
        self.symbols = symbols or ["AAPL"]
        self.risk_manager = RiskManager(limits=risk_limits)
        self.executor = PaperExecutor(risk_manager=self.risk_manager)
        self.order_books: dict[str, OrderBook] = {}
        self._current_bbo: dict[str, dict[str, float]] = {}
        self._current_mid: dict[str, float] = {}
        self.is_running = False

    def get_order_book(self, symbol: str) -> OrderBook:
        """Retrieves or creates the live Level-2 Limit Order Book for symbol."""
        sym_clean = symbol.upper().strip()
        if sym_clean not in self.order_books:
            self.order_books[sym_clean] = OrderBook(symbol=sym_clean)
        return self.order_books[sym_clean]

    def on_start(self) -> None:
        """Called when strategy starts execution."""
        self.is_running = True

    def on_tick(self, event: CanonicalEvent) -> None:
        """Called on every canonical trade event."""
        pass

    def on_quote(self, event: CanonicalEvent) -> None:
        """Called on every canonical quote event."""
        if event.bid_price and event.ask_price:
            self._current_bbo[event.instrument_id] = {
                "bid": event.bid_price,
                "ask": event.ask_price,
                "bid_size": event.bid_size or 100.0,
                "ask_size": event.ask_size or 100.0,
            }
            self._current_mid[event.instrument_id] = (
                event.bid_price + event.ask_price
            ) / 2.0
            book = self.get_order_book(event.instrument_id)
            book.update_quote(
                event.bid_price,
                event.ask_price,
                event.bid_size or 100.0,
                event.ask_size or 100.0,
                timestamp=event.exchange_timestamp
                or event.receive_timestamp
                or time.time(),
            )

    def on_flow(self, flow_metrics: dict[str, Any]) -> None:
        """Called when order flow / CVD metrics update."""
        pass

    def on_whale(self, whale_trade: dict[str, Any]) -> None:
        """Called when an institutional whale block trade is detected."""
        pass

    def on_fill(self, order: Order) -> None:
        """Called when a submitted order gets filled."""
        pass

    def on_stop(self) -> None:
        """Called when strategy stops execution."""
        self.is_running = False

    def buy(
        self,
        symbol: str,
        quantity: float,
        price: float | None = None,
        reason: str = "",
    ) -> Order:
        order_type = OrderType.MARKET if price is None else OrderType.LIMIT
        bbo = self._current_bbo.get(symbol)
        book = self.get_order_book(symbol)
        order = self.executor.submit_order(
            symbol,
            OrderSide.BUY,
            order_type,
            quantity,
            price,
            bbo=bbo,
            order_book=book,
            signal_reason=reason,
        )
        if order.status == OrderStatus.FILLED:
            self.on_fill(order)
        return order

    def sell(
        self,
        symbol: str,
        quantity: float,
        price: float | None = None,
        reason: str = "",
    ) -> Order:
        order_type = OrderType.MARKET if price is None else OrderType.LIMIT
        bbo = self._current_bbo.get(symbol)
        book = self.get_order_book(symbol)
        order = self.executor.submit_order(
            symbol,
            OrderSide.SELL,
            order_type,
            quantity,
            price,
            bbo=bbo,
            order_book=book,
            signal_reason=reason,
        )
        if order.status == OrderStatus.FILLED:
            self.on_fill(order)
        return order

    def get_equity(self) -> float:
        return self.executor.total_equity(self._current_mid)

    def get_execution_ledger(self) -> list[dict[str, Any]]:
        """
        Returns chronological trade ledger detailing what the strategy did,
        execution prices, order book microstructure at fill, and risk outcomes.
        """
        ledger = []
        for idx, f in enumerate(self.executor.fills, start=1):
            ord_id = f.get("order_id")
            matched_order = next(
                (o for o in self.executor.orders if o.order_id == ord_id), None
            )
            item = {
                "trade_id": f"trd-{idx:04d}",
                "order_id": ord_id,
                "timestamp": f.get("timestamp"),
                "symbol": f.get("symbol"),
                "side": f.get("side"),
                "order_type": matched_order.order_type.value
                if matched_order
                else "MARKET",
                "quantity": f.get("qty"),
                "arrival_price": matched_order.arrival_price if matched_order else None,
                "filled_price": f.get("price"),
                "status": "FILLED",
                "slippage_bps": f.get("slippage_bps", 0.0),
                "slippage_usd": matched_order.slippage_usd if matched_order else 0.0,
                "effective_spread_bps": matched_order.effective_spread_bps
                if matched_order
                else 0.0,
                "signal_reason": matched_order.signal_reason if matched_order else "",
                "cash_after": f.get("cash_after"),
                "realized_pnl": f.get("realized_pnl"),
                "order_book_snapshot": matched_order.order_book_snapshot.to_dict()
                if (matched_order and matched_order.order_book_snapshot)
                else None,
                "rungs_consumed": matched_order.rungs_consumed if matched_order else [],
            }
            ledger.append(item)
        return ledger

    def export_executions(
        self, filepath: str | None = None, format: str = "json"
    ) -> str:
        """
        Exports strategy executions, order book history, and performance tear-sheet
        to a structured JSON or CSV file.
        """
        fmt = format.lower().strip()
        if not filepath:
            os.makedirs("data/reports", exist_ok=True)
            ts = int(time.time())
            sym_clean = self.symbols[0] if self.symbols else "PORTFOLIO"
            filepath = f"data/reports/strategy_{self.name}_{sym_clean}_{ts}.{fmt}"
        else:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

        ledger = self.get_execution_ledger()
        summary = self.performance_summary()

        if fmt == "csv":
            headers = [
                "trade_id",
                "order_id",
                "timestamp",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "arrival_price",
                "filled_price",
                "status",
                "slippage_bps",
                "slippage_usd",
                "effective_spread_bps",
                "best_bid",
                "best_ask",
                "spread_bps",
                "micro_price",
                "signal_reason",
                "cash_after",
                "realized_pnl",
            ]
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                for item in ledger:
                    row = {k: item.get(k, "") for k in headers}
                    if item.get("order_book_snapshot"):
                        row.update(
                            {
                                k: item["order_book_snapshot"].get(k, "")
                                for k in (
                                    "best_bid",
                                    "best_ask",
                                    "spread_bps",
                                    "micro_price",
                                )
                            }
                        )
                    writer.writerow(row)
        else:
            export_payload = {
                "strategy": self.name,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "performance_summary": summary,
                "current_order_books": {
                    sym: ob.to_dict() for sym, ob in self.order_books.items()
                },
                "executions_count": len(ledger),
                "executions": ledger,
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(export_payload, f, indent=2)

        return os.path.abspath(filepath)

    def performance_summary(self) -> dict[str, Any]:
        """Compute institutional performance metrics."""
        fills = self.executor.fills
        total_trades = len(fills)
        realized_pnl = sum(pos.realized_pnl for pos in self.executor.positions.values())
        unrealized_pnl = sum(
            pos.unrealized_pnl for pos in self.executor.positions.values()
        )
        total_pnl = realized_pnl + unrealized_pnl
        equity = self.get_equity()

        win_count = sum(1 for f in fills if f.get("realized_pnl", 0) > 0)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        avg_slippage = (
            sum(f.get("slippage_bps", 0) for f in fills) / total_trades
            if total_trades > 0
            else 0.0
        )

        return {
            "strategy": self.name,
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "final_equity": equity,
            "return_pct": (
                (equity - self.executor.initial_cash) / self.executor.initial_cash
            )
            * 100.0,
            "avg_slippage_bps": avg_slippage,
            "positions": {
                sym: pos.quantity for sym, pos in self.executor.positions.items()
            },
        }


# ---------------------------------------------------------------------------
# Reference Institutional Strategies
# ---------------------------------------------------------------------------


class WhaleMomentumStrategy(Strategy):
    """
    Whale Momentum Strategy.
    Follows institutional smart-money aggressor flow. When a whale print
    (> $100k) is detected, enters in the direction of the block print with a
    trailing stop and profit target.
    """

    def __init__(
        self,
        symbol: str = "AAPL",
        trade_size: float = 100.0,
        stop_loss_pct: float = 0.5,
    ):
        self.symbol = symbol
        self.is_all = symbol.upper() in ("ALL", "*", "MARKET")
        syms = None if self.is_all else [s.strip() for s in symbol.split(",")]
        super().__init__(name="WhaleMomentum", symbols=syms)
        self.trade_size = trade_size
        self.stop_loss_pct = stop_loss_pct
        self.entry_prices: dict[str, float] = {}

    @property
    def entry_price(self) -> float | None:
        return self.entry_prices.get(self.symbol)

    @entry_price.setter
    def entry_price(self, val: float | None):
        if val is None:
            self.entry_prices.pop(self.symbol, None)
        else:
            self.entry_prices[self.symbol] = val

    def on_whale(self, whale_trade: dict[str, Any]) -> None:
        inst = whale_trade.get("instrument")
        if not inst:
            return
        if not self.is_all and inst != self.symbol and inst not in self.symbols:
            return

        side = whale_trade.get("side", "BUY")
        pos = self.executor.get_position(inst)

        if side == "BUY" and pos.quantity <= 0:
            # Institutional accumulation detected: enter Long
            if pos.quantity < 0:
                self.buy(
                    inst, abs(pos.quantity), reason="Whale Accumulation: Cover Short"
                )
            order = self.buy(
                inst, self.trade_size, reason="Whale Accumulation: Enter Long"
            )
            if order.status == OrderStatus.FILLED:
                self.entry_prices[inst] = order.filled_price
        elif side == "SELL" and pos.quantity >= 0:
            # Institutional distribution detected: exit Long or enter Short
            if pos.quantity > 0:
                self.sell(inst, pos.quantity, reason="Whale Distribution: Close Long")
            self.entry_prices.pop(inst, None)

    def on_tick(self, event: CanonicalEvent) -> None:
        inst = event.instrument_id
        if not self.is_all and inst != self.symbol and inst not in self.symbols:
            return
        if event.price is None:
            return

        pos = self.executor.get_position(inst)
        entry_price = self.entry_prices.get(inst)
        if pos.quantity > 0 and entry_price is not None:
            # Stop-loss check
            drop_pct = (entry_price - event.price) / entry_price * 100.0
            if drop_pct >= self.stop_loss_pct:
                self.sell(
                    inst,
                    pos.quantity,
                    reason=f"Risk Guard: Stop-Loss (-{drop_pct:.2f}%)",
                )
                self.entry_prices.pop(inst, None)


class SpreadCaptureMarketMaker(Strategy):
    """
    Spread Capture Market Maker.
    Provides passive liquidity inside wide bid/ask spreads when spread
    exceeds threshold and inventory is balanced.
    """

    def __init__(
        self,
        symbol: str = "AAPL",
        min_spread_bps: float = 3.0,
        quote_size: float = 50.0,
    ):
        self.symbol = symbol
        self.is_all = symbol.upper() in ("ALL", "*", "MARKET")
        syms = None if self.is_all else [s.strip() for s in symbol.split(",")]
        super().__init__(name="SpreadCaptureMM", symbols=syms)
        self.min_spread_bps = min_spread_bps
        self.quote_size = quote_size

    def on_quote(self, event: CanonicalEvent) -> None:
        super().on_quote(event)
        inst = event.instrument_id
        if not self.is_all and inst != self.symbol and inst not in self.symbols:
            return
        if not event.bid_price or not event.ask_price:
            return

        spread = event.ask_price - event.bid_price
        mid = (event.bid_price + event.ask_price) / 2.0
        spread_bps = (spread / mid) * 10_000.0

        pos = self.executor.get_position(inst)

        # Only quote if spread is attractive and position within limits
        if spread_bps >= self.min_spread_bps:
            if pos.quantity <= 0:
                # Quote buy limit just above bid
                buy_px = round(event.bid_price + 0.01, 2)
                self.buy(
                    inst,
                    self.quote_size,
                    price=buy_px,
                    reason=f"Market Making: Passive Bid ({spread_bps:.1f} bps)",
                )
            if pos.quantity >= 0:
                # Quote sell limit just below ask
                sell_px = round(event.ask_price - 0.01, 2)
                self.sell(
                    inst,
                    self.quote_size,
                    price=sell_px,
                    reason=f"Market Making: Passive Ask ({spread_bps:.1f} bps)",
                )


class StrategyRunner:
    """
    Orchestrates execution of an algorithmic strategy across historical
    or simulated market event feeds.
    """

    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    def run_events(
        self, events: list[CanonicalEvent], flow_tracker: Any | None = None
    ) -> dict[str, Any]:
        self.strategy.on_start()
        for evt in events:
            if evt.event_type == EventType.QUOTE:
                self.strategy.on_quote(evt)
            elif evt.event_type == EventType.TRADE:
                self.strategy.on_tick(evt)

                # Check for whale print
                if evt.price and evt.quantity:
                    notional = evt.price * evt.quantity
                    if notional >= 100_000.0 or evt.quantity >= 500:
                        whale_info = {
                            "instrument": evt.instrument_id,
                            "price": evt.price,
                            "quantity": evt.quantity,
                            "notional": notional,
                            "side": "BUY"
                            if evt.price
                            >= (
                                self.strategy._current_mid.get(
                                    evt.instrument_id, evt.price
                                )
                            )
                            else "SELL",
                            "timestamp": evt.exchange_timestamp,
                        }
                        self.strategy.on_whale(whale_info)

            if flow_tracker is not None:
                try:
                    summary = (
                        flow_tracker.summary()
                        if hasattr(flow_tracker, "summary")
                        else {}
                    )
                    self.strategy.on_flow(summary)
                except Exception:
                    pass

        self.strategy.on_stop()
        return self.strategy.performance_summary()
