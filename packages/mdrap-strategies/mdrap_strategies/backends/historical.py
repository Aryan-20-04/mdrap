"""
Backend A — Historical Simulation Execution Backend (§26, v5.1.1).

Consumes recorded market events or synthetic historical feeds and replays them
deterministically through the RealisticMatchingEngine.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

from .base import ExecutionBackend
from ..matching_engine import (
    MatchingEngineConfig,
    RealisticMatchingEngine,
    SimulatedOrder,
    SimOrderSide,
    SimOrderStatus,
    SimOrderType,
)
from ..risk_engine import InstitutionalRiskEngine


class HistoricalSimulationBackend(ExecutionBackend):
    """
    Backend A: Deterministic Historical Simulation.
    All order fills are generated locally by the RealisticMatchingEngine based on
    replayed market depth and trade flow.
    """

    def __init__(
        self,
        risk_engine: InstitutionalRiskEngine,
        matching_config: MatchingEngineConfig | None = None,
        initial_cash: float = 100_000.0,
    ):
        super().__init__(name="HISTORICAL_SIMULATION", risk_engine=risk_engine)
        self.matching_engine = RealisticMatchingEngine(config=matching_config)
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self._order_counter = itertools.count(1)
        self.current_time = 0.0
        self.equity_curve: list[tuple[float, float]] = []

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def submit_order(
        self,
        symbol: str,
        side: SimOrderSide,
        order_type: SimOrderType,
        quantity: float,
        price: float | None = None,
        signal_reason: str = "",
    ) -> SimulatedOrder | None:
        ord_id = f"hist-ord-{next(self._order_counter)}"
        mid = self.matching_engine._mid_prices.get(symbol, price or 100.0)

        # Pre-trade risk validation
        risk_res = self.risk_engine.validate_order(
            symbol=symbol,
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            price=price,
            prevailing_mid=mid,
            venue="HISTORICAL",
            idempotency_key=f"{symbol}_{side.value}_{quantity}_{price}_{int(self.current_time)}",
        )
        if not risk_res.passed:
            rejected_order = SimulatedOrder(
                order_id=ord_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                decision_timestamp=self.current_time,
                status=SimOrderStatus.REJECTED,
                reject_reason=risk_res.reason,
                signal_reason=signal_reason,
            )
            self.matching_engine.orders[ord_id] = rejected_order
            return rejected_order

        # Submit to matching engine
        order = self.matching_engine.submit_order(
            order_id=ord_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            current_time=self.current_time,
            signal_reason=signal_reason,
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        return self.matching_engine.cancel_order(order_id, current_time=self.current_time)

    def on_market_event(self, event: Any) -> list[dict[str, Any]]:
        """Process incoming historical canonical event."""
        ev_ts = getattr(event, "exchange_timestamp", None) or getattr(event, "receive_timestamp", None) or self.current_time
        self.current_time = max(self.current_time, ev_ts)
        sym = getattr(event, "instrument_id", "AAPL")
        ev_type = str(getattr(event, "event_type", "QUOTE")).upper()

        fills = []

        if "QUOTE" in ev_type:
            bids = []
            asks = []
            if getattr(event, "bid_price", None) and getattr(event, "bid_size", None):
                bids.append((event.bid_price, event.bid_size))
            if getattr(event, "ask_price", None) and getattr(event, "ask_size", None):
                asks.append((event.ask_price, event.ask_size))
            self.matching_engine.update_book(sym, bids, asks, self.current_time)
            # Advance matching engine time slice
            fills = self.matching_engine.process_time_slice(self.current_time)

        elif "TRADE" in ev_type:
            px = getattr(event, "price", None)
            sz = getattr(event, "quantity", 100.0)
            if px is not None and sz is not None:
                fills = self.matching_engine.process_market_trade(sym, px, sz, self.current_time)

        # Update cash, inventory, and risk state for every fill
        for fill in fills:
            if fill.get("event") == "FILL":
                cost = fill["price"] * fill["quantity"]
                fee = fill.get("fee", 0.0)
                if fill["side"] == "BUY":
                    self.cash -= (cost + fee)
                else:
                    self.cash += (cost - fee)

                self.risk_engine.update_position(
                    symbol=fill["symbol"],
                    fill_qty=fill["quantity"],
                    fill_price=fill["price"],
                    side=fill["side"],
                    venue="HISTORICAL",
                )

        eq = self.get_equity()
        self.risk_engine.update_portfolio_equity(eq)
        self.equity_curve.append((self.current_time, eq))
        return fills

    def get_positions(self) -> dict[str, float]:
        return dict(self.risk_engine.positions)

    def get_equity(self) -> float:
        unrealized = sum(
            qty * self.matching_engine._mid_prices.get(sym, self.risk_engine.avg_costs.get(sym, 100.0))
            for sym, qty in self.risk_engine.positions.items()
        )
        return self.cash + unrealized

    def get_open_orders(self) -> list[SimulatedOrder]:
        return [
            o for o in self.matching_engine.orders.values()
            if o.status in (SimOrderStatus.ACCEPTED, SimOrderStatus.PENDING, SimOrderStatus.PARTIALLY_FILLED)
        ]
