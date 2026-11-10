"""
MDRAP Execution Backend Abstract Base Class (§26, v5.1.1).
"""

from __future__ import annotations

import abc
from typing import Any

from ..matching_engine import SimulatedOrder, SimOrderSide, SimOrderType
from ..risk_engine import InstitutionalRiskEngine


class ExecutionBackend(abc.ABC):
    """Abstract base class for all execution backends."""

    def __init__(self, name: str, risk_engine: InstitutionalRiskEngine):
        self.name = name
        self.risk_engine = risk_engine
        self.is_running = False

    @abc.abstractmethod
    def start(self) -> None:
        """Start backend."""
        pass

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop backend."""
        pass

    @abc.abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: SimOrderSide,
        order_type: SimOrderType,
        quantity: float,
        price: float | None = None,
        signal_reason: str = "",
    ) -> SimulatedOrder | None:
        """Submit an order through pre-trade risk checks to the backend."""
        pass

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing resting order."""
        pass

    @abc.abstractmethod
    def on_market_event(self, event: Any) -> list[dict[str, Any]]:
        """Feed a market event (quote/trade) to update order book and matching engine."""
        pass

    @abc.abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Return current portfolio positions."""
        pass

    @abc.abstractmethod
    def get_equity(self) -> float:
        """Return total portfolio equity."""
        pass
