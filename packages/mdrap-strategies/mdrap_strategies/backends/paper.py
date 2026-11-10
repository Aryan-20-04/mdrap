"""
Backend B — Live-Market Paper Trading Execution Backend (§26, v5.1.1).

Consumes current public exchange market data (WebSockets / REST) but never submits
real orders to external exchanges. All order matching is performed locally by the
RealisticMatchingEngine against live market quotes and trade tape.
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


class LivePaperTradingBackend(ExecutionBackend):
    """
    Backend B: Live-Market Paper Trading.
    All orders are risk-checked and simulated against live incoming exchange feeds.
    """

    def __init__(
        self,
        risk_engine: InstitutionalRiskEngine,
        matching_config: MatchingEngineConfig | None = None,
        initial_cash: float = 100_000.0,
    ):
        super().__init__(name="LIVE_PAPER_TRADING", risk_engine=risk_engine)
        self.matching_engine = RealisticMatchingEngine(config=matching_config)
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self._order_counter = itertools.count(1)
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
        now = time.time()
        ord_id = f"paper-ord-{next(self._order_counter)}"
        mid = self.matching_engine._mid_prices.get(symbol, price or 100.0)

        # Pre-trade risk evaluation
        risk_res = self.risk_engine.validate_order(
            symbol=symbol,
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            price=price,
            prevailing_mid=mid,
            venue="LIVE_PAPER",
            idempotency_key=f"paper_{symbol}_{side.value}_{quantity}_{price}_{int(now)}",
        )
        if not risk_res.passed:
            rejected = SimulatedOrder(
                order_id=ord_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                decision_timestamp=now,
                status=SimOrderStatus.REJECTED,
                reject_reason=risk_res.reason,
                signal_reason=signal_reason,
            )
            self.matching_engine.orders[ord_id] = rejected
            return rejected

        order = self.matching_engine.submit_order(
            order_id=ord_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            current_time=now,
            signal_reason=signal_reason,
        )
        return order

    def cancel_order(self, order_id: str) -> bool:
        return self.matching_engine.cancel_order(order_id, current_time=time.time())

    def on_market_event(self, event: Any) -> list[dict[str, Any]]:
        now = time.time()
        sym = getattr(event, "instrument_id", None) or (
            event.payload.get("instrument") if hasattr(event, "payload") and isinstance(event.payload, dict) else "BTC/USD"
        )
        ev_type = str(getattr(event, "event_type", None) or (
            event.payload.get("event_type") if hasattr(event, "payload") and isinstance(event.payload, dict) else "QUOTE"
        )).upper()

        fills = []

        if "QUOTE" in ev_type:
            bids = []
            asks = []
            # Check canonical or raw payload
            b_px = getattr(event, "bid_price", None) or (event.payload.get("bid") if hasattr(event, "payload") else None)
            a_px = getattr(event, "ask_price", None) or (event.payload.get("ask") if hasattr(event, "payload") else None)
            b_sz = getattr(event, "bid_size", None) or (event.payload.get("bid_size") if hasattr(event, "payload") else 1.0)
            a_sz = getattr(event, "ask_size", None) or (event.payload.get("ask_size") if hasattr(event, "payload") else 1.0)

            if b_px and b_sz:
                bids.append((float(b_px), float(b_sz)))
            if a_px and a_sz:
                asks.append((float(a_px), float(a_sz)))

            if bids or asks:
                self.matching_engine.update_book(sym, bids, asks, now)
                fills = self.matching_engine.process_time_slice(now)

        elif "TRADE" in ev_type:
            px = getattr(event, "price", None) or (event.payload.get("price") if hasattr(event, "payload") else None)
            sz = getattr(event, "quantity", None) or (event.payload.get("quantity") if hasattr(event, "payload") else 1.0)
            if px is not None and sz is not None:
                fills = self.matching_engine.process_market_trade(sym, float(px), float(sz), now)

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
                    venue="LIVE_PAPER",
                )

        eq = self.get_equity()
        self.risk_engine.update_portfolio_equity(eq)
        self.equity_curve.append((now, eq))
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
