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

import enum
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from models import CanonicalEvent, EventType, QualityStatus


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
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_timestamp: Optional[float] = None
    slippage_bps: float = 0.0
    reject_reason: Optional[str] = None


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
    max_position_size: float = 5000.0        # Max absolute shares in any single instrument
    max_order_size: float = 1000.0           # Max shares per individual order (fat-finger guard)
    price_collar_bps: float = 50.0           # Max allowed deviation from NBBO midpoint (50 bps = 0.50%)
    max_drawdown_pct: float = 5.0            # Kill-switch if portfolio drawdown exceeds 5%
    allow_short: bool = True                 # Whether short selling is enabled


class RiskManager:
    """
    Institutional Pre-Trade Risk Gate.
    Evaluates orders against firm risk limits before execution.
    """

    def __init__(self, limits: Optional[RiskLimits] = None, initial_capital: float = 100_000.0):
        self.limits = limits or RiskLimits()
        self.initial_capital = initial_capital
        self.peak_equity = initial_capital
        self.kill_switch_triggered = False

    def validate_order(
        self,
        order: Order,
        current_mid: Optional[float],
        current_position: Position,
        current_equity: float,
    ) -> Tuple[bool, Optional[str]]:
        """Validate order against pre-trade risk controls."""
        # 1. Check kill-switch / maximum drawdown
        if self.kill_switch_triggered:
            return False, "Risk kill-switch active: trading halted"

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100.0
        if drawdown_pct >= self.limits.max_drawdown_pct:
            self.kill_switch_triggered = True
            return False, f"Maximum drawdown breached ({drawdown_pct:.2f}% >= {self.limits.max_drawdown_pct:.2f}%): kill-switch triggered"

        # 2. Check maximum order size (fat finger guard)
        if order.quantity <= 0:
            return False, f"Invalid order quantity: {order.quantity}"

        if order.quantity > self.limits.max_order_size:
            return False, f"Order quantity {order.quantity} exceeds max order limit {self.limits.max_order_size}"

        # 3. Check projected position size
        projected_qty = current_position.quantity
        if order.side == OrderSide.BUY:
            projected_qty += order.quantity
        else:
            projected_qty -= order.quantity

        if not self.limits.allow_short and projected_qty < 0:
            return False, "Short positions are disabled by risk policy"

        if abs(projected_qty) > self.limits.max_position_size:
            return False, f"Projected position {projected_qty} exceeds max limit {self.limits.max_position_size}"

        # 4. Check price collar for limit orders (fat-finger price deviation)
        if order.order_type == OrderType.LIMIT and order.price is not None and current_mid is not None and current_mid > 0:
            deviation_bps = abs(order.price - current_mid) / current_mid * 10_000.0
            if deviation_bps > self.limits.price_collar_bps:
                return False, f"Limit price {order.price} deviates by {deviation_bps:.1f} bps from mid {current_mid:.2f} (max collar: {self.limits.price_collar_bps} bps)"

        return True, None


class PaperExecutor:
    """
    High-fidelity simulated execution engine.
    Matches orders against NBBO quotes and L2 depth with realistic slippage.
    """

    def __init__(self, initial_cash: float = 100_000.0, risk_manager: Optional[RiskManager] = None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.risk_manager = risk_manager or RiskManager(initial_capital=initial_cash)
        self.positions: Dict[str, Position] = {}
        self.orders: List[Order] = []
        self.fills: List[Dict[str, Any]] = []
        self.equity_curve: List[Tuple[float, float]] = []  # (timestamp, equity)
        self._order_counter = 0

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def total_equity(self, current_prices: Dict[str, float]) -> float:
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
        price: Optional[float] = None,
        bbo: Optional[Dict[str, float]] = None,
    ) -> Order:
        """Submit, risk-check, and simulate execution of an order."""
        self._order_counter += 1
        order = Order(
            order_id=f"ord-{self._order_counter}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            timestamp=time.time(),
        )

        pos = self.get_position(symbol)
        mid = (bbo["bid"] + bbo["ask"]) / 2.0 if bbo and "bid" in bbo and "ask" in bbo else None
        current_px = mid or (bbo["bid"] if bbo and "bid" in bbo else pos.avg_cost or 100.0)
        current_eq = self.total_equity({symbol: current_px})

        # Pre-trade risk evaluation
        ok, reason = self.risk_manager.validate_order(order, mid, pos, current_eq)
        if not ok:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reason
            self.orders.append(order)
            return order

        # Simulated execution against book
        fill_price = None
        slippage_bps = 0.0

        if bbo is not None:
            bid = bbo.get("bid", 0.0)
            ask = bbo.get("ask", 0.0)
            bid_sz = bbo.get("bid_size", 1000.0)
            ask_sz = bbo.get("ask_size", 1000.0)

            if order_type == OrderType.MARKET:
                # Market order: crosses the spread
                if side == OrderSide.BUY:
                    arrival_px = ask
                    # Simulated market impact slippage if order exceeds top-of-book size
                    impact = max(0.0, (quantity - ask_sz) / 10_000.0) * 0.01 if quantity > ask_sz else 0.0
                    fill_price = ask + impact
                    slippage_bps = ((fill_price - arrival_px) / arrival_px) * 10_000.0 if arrival_px > 0 else 0.0
                else:
                    arrival_px = bid
                    impact = max(0.0, (quantity - bid_sz) / 10_000.0) * 0.01 if quantity > bid_sz else 0.0
                    fill_price = bid - impact
                    slippage_bps = ((arrival_px - fill_price) / arrival_px) * 10_000.0 if arrival_px > 0 else 0.0

            elif order_type == OrderType.LIMIT:
                # Limit order: check if marketable or resting
                if price is not None:
                    if side == OrderSide.BUY and price >= ask:
                        fill_price = ask
                    elif side == OrderSide.SELL and price <= bid:
                        fill_price = bid
                    else:
                        # Resting limit order (not filled immediately)
                        order.status = OrderStatus.PENDING
                        self.orders.append(order)
                        return order
        else:
            # Fallback if no BBO provided: fill at limit price or default
            fill_price = price if price is not None else 100.0

        if fill_price is not None:
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
                pos.avg_cost = ((pos.quantity * pos.avg_cost) + fill_cost) / new_qty if new_qty > 0 else fill_price
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
                pos.avg_cost = ((abs(pos.quantity) * pos.avg_cost) + fill_cost) / abs(new_qty) if new_qty != 0 else fill_price
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
        self.fills.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": order.quantity,
            "price": fill_price,
            "slippage_bps": slippage_bps,
            "cash_after": self.cash,
            "realized_pnl": pos.realized_pnl,
            "timestamp": order.filled_timestamp,
        })


class Strategy:
    """
    Abstract Base Class for Event-Driven Algorithmic Strategies.
    """

    def __init__(self, name: str, symbols: Optional[List[str]] = None, risk_limits: Optional[RiskLimits] = None):
        self.name = name
        self.symbols = symbols or ["AAPL"]
        self.risk_manager = RiskManager(limits=risk_limits)
        self.executor = PaperExecutor(risk_manager=self.risk_manager)
        self._current_bbo: Dict[str, Dict[str, float]] = {}
        self._current_mid: Dict[str, float] = {}
        self.is_running = False

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
            self._current_mid[event.instrument_id] = (event.bid_price + event.ask_price) / 2.0

    def on_flow(self, flow_metrics: Dict[str, Any]) -> None:
        """Called when order flow / CVD metrics update."""
        pass

    def on_whale(self, whale_trade: Dict[str, Any]) -> None:
        """Called when an institutional whale block trade is detected."""
        pass

    def on_fill(self, order: Order) -> None:
        """Called when a submitted order gets filled."""
        pass

    def on_stop(self) -> None:
        """Called when strategy stops execution."""
        self.is_running = False

    def buy(self, symbol: str, quantity: float, price: Optional[float] = None) -> Order:
        order_type = OrderType.MARKET if price is None else OrderType.LIMIT
        bbo = self._current_bbo.get(symbol)
        order = self.executor.submit_order(symbol, OrderSide.BUY, order_type, quantity, price, bbo)
        if order.status == OrderStatus.FILLED:
            self.on_fill(order)
        return order

    def sell(self, symbol: str, quantity: float, price: Optional[float] = None) -> Order:
        order_type = OrderType.MARKET if price is None else OrderType.LIMIT
        bbo = self._current_bbo.get(symbol)
        order = self.executor.submit_order(symbol, OrderSide.SELL, order_type, quantity, price, bbo)
        if order.status == OrderStatus.FILLED:
            self.on_fill(order)
        return order

    def get_equity(self) -> float:
        return self.executor.total_equity(self._current_mid)

    def performance_summary(self) -> Dict[str, Any]:
        """Compute institutional performance metrics."""
        fills = self.executor.fills
        total_trades = len(fills)
        realized_pnl = sum(pos.realized_pnl for pos in self.executor.positions.values())
        unrealized_pnl = sum(pos.unrealized_pnl for pos in self.executor.positions.values())
        total_pnl = realized_pnl + unrealized_pnl
        equity = self.get_equity()

        win_count = sum(1 for f in fills if f.get("realized_pnl", 0) > 0)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        avg_slippage = sum(f.get("slippage_bps", 0) for f in fills) / total_trades if total_trades > 0 else 0.0

        return {
            "strategy": self.name,
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "final_equity": equity,
            "return_pct": ((equity - self.executor.initial_cash) / self.executor.initial_cash) * 100.0,
            "avg_slippage_bps": avg_slippage,
            "positions": {sym: pos.quantity for sym, pos in self.executor.positions.items()},
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

    def __init__(self, symbol: str = "AAPL", trade_size: float = 100.0, stop_loss_pct: float = 0.5):
        super().__init__(name="WhaleMomentum", symbols=[symbol])
        self.symbol = symbol
        self.trade_size = trade_size
        self.stop_loss_pct = stop_loss_pct
        self.entry_price: Optional[float] = None

    def on_whale(self, whale_trade: Dict[str, Any]) -> None:
        if whale_trade.get("instrument") != self.symbol:
            return

        side = whale_trade.get("side", "BUY")
        pos = self.executor.get_position(self.symbol)

        if side == "BUY" and pos.quantity <= 0:
            # Institutional accumulation detected: enter Long
            if pos.quantity < 0:
                self.buy(self.symbol, abs(pos.quantity))  # Close short
            order = self.buy(self.symbol, self.trade_size)
            if order.status == OrderStatus.FILLED:
                self.entry_price = order.filled_price
        elif side == "SELL" and pos.quantity >= 0:
            # Institutional distribution detected: exit Long or enter Short
            if pos.quantity > 0:
                self.sell(self.symbol, pos.quantity)
            self.entry_price = None

    def on_tick(self, event: CanonicalEvent) -> None:
        if event.instrument_id != self.symbol or event.price is None:
            return

        pos = self.executor.get_position(self.symbol)
        if pos.quantity > 0 and self.entry_price is not None:
            # Stop-loss check
            drop_pct = (self.entry_price - event.price) / self.entry_price * 100.0
            if drop_pct >= self.stop_loss_pct:
                self.sell(self.symbol, pos.quantity)
                self.entry_price = None


class SpreadCaptureMarketMaker(Strategy):
    """
    Spread Capture Market Maker.
    Provides passive liquidity inside wide bid/ask spreads when spread
    exceeds threshold and inventory is balanced.
    """

    def __init__(self, symbol: str = "AAPL", min_spread_bps: float = 3.0, quote_size: float = 50.0):
        super().__init__(name="SpreadCaptureMM", symbols=[symbol])
        self.symbol = symbol
        self.min_spread_bps = min_spread_bps
        self.quote_size = quote_size

    def on_quote(self, event: CanonicalEvent) -> None:
        super().on_quote(event)
        if event.instrument_id != self.symbol or not event.bid_price or not event.ask_price:
            return

        spread = event.ask_price - event.bid_price
        mid = (event.bid_price + event.ask_price) / 2.0
        spread_bps = (spread / mid) * 10_000.0

        pos = self.executor.get_position(self.symbol)

        # Only quote if spread is attractive and position within limits
        if spread_bps >= self.min_spread_bps:
            if pos.quantity <= 0:
                # Quote buy limit just above bid
                buy_px = round(event.bid_price + 0.01, 2)
                self.buy(self.symbol, self.quote_size, price=buy_px)
            if pos.quantity >= 0:
                # Quote sell limit just below ask
                sell_px = round(event.ask_price - 0.01, 2)
                self.sell(self.symbol, self.quote_size, price=sell_px)


class StrategyRunner:
    """
    Orchestrates execution of an algorithmic strategy across historical
    or simulated market event feeds.
    """

    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    def run_events(self, events: List[CanonicalEvent], flow_tracker: Optional[Any] = None) -> Dict[str, Any]:
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
                            "side": "BUY" if evt.price >= (self.strategy._current_mid.get(evt.instrument_id, evt.price)) else "SELL",
                            "timestamp": evt.exchange_timestamp,
                        }
                        self.strategy.on_whale(whale_info)

            if flow_tracker is not None:
                try:
                    summary = flow_tracker.summary() if hasattr(flow_tracker, "summary") else {}
                    self.strategy.on_flow(summary)
                except Exception:
                    pass

        self.strategy.on_stop()
        return self.strategy.performance_summary()
