"""MDRAP transaction cost analysis module — delegates to companion package mdrap_analytics."""

from __future__ import annotations

import os
import sys
import warnings

warnings.warn(
    "TCAEngine and mdrap.tca are deprecated and non-core to the market data engine; "
    "use mdrap_analytics companion package instead.",
    DeprecationWarning,
    stacklevel=2,
)

_impl = None
try:
    import mdrap_analytics as _impl
except ImportError:
    _pkg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "packages",
        "mdrap-analytics",
    )
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    try:
        import mdrap_analytics as _impl
    except ImportError:
        _impl = None

__stability__ = "experimental"

if _impl is not None:
    sys.modules[__name__] = _impl
else:

    class ExecutionRecord:
        def __init__(
            self,
            trade_id: str = "",
            symbol: str = "AAPL",
            side: str = "BUY",
            price: float = 0.0,
            shares: float = 0.0,
            timestamp: float = 0.0,
            broker: str = "DEFAULT",
            venue: str = "NASDAQ",
            order_type: str = "MARKET",
            arrival_price: float = 0.0,
            *args,
            **kwargs,
        ):
            self.trade_id = trade_id
            self.symbol = symbol
            self.side = side
            self.price = price
            self.shares = shares
            self.timestamp = timestamp
            self.broker = broker
            self.venue = venue
            self.order_type = order_type
            self.arrival_price = arrival_price

        def to_dict(self):
            return {
                "trade_id": self.trade_id,
                "symbol": self.symbol,
                "side": self.side,
                "price": self.price,
                "shares": self.shares,
                "timestamp": self.timestamp,
                "broker": self.broker,
                "venue": self.venue,
                "order_type": self.order_type,
                "arrival_price": self.arrival_price,
            }

    class TCAMetrics:
        def __init__(self, *args, **kwargs):
            pass

        def to_dict(self):
            return {}

    class BrokerScorecard:
        def __init__(self, *args, **kwargs):
            pass

        def to_dict(self):
            return {}

    def generate_demo_executions(
        symbol: str = "AAPL", count: int = 100, seed: int | None = None
    ) -> list[ExecutionRecord]:
        import time

        records: list[ExecutionRecord] = []
        t_now = time.time() - 3600.0
        for i in range(count):
            records.append(
                ExecutionRecord(
                    trade_id=f"EXEC-{symbol}-{i + 1:04d}",
                    symbol=symbol.upper(),
                    side="BUY" if (i % 2 == 0) else "SELL",
                    price=150.0 + (i * 0.05),
                    shares=100.0,
                    timestamp=t_now + (i * 30.0),
                    broker="Demo Broker",
                    venue="NASDAQ",
                    order_type="LIMIT",
                    arrival_price=150.0,
                )
            )
        return records

    class TCAEngine:
        _is_stub = True

        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "TCAEngine requires the 'mdrap_analytics' companion package. "
                "Install packages/mdrap-analytics to use Transaction Cost Analysis features."
            )

        @staticmethod
        def generate_demo_executions(
            symbol: str = "AAPL", count: int = 100, seed: int | None = None
        ) -> list[ExecutionRecord]:
            return generate_demo_executions(symbol=symbol, count=count, seed=seed)

    __all__ = [
        "TCAEngine",
        "ExecutionRecord",
        "TCAMetrics",
        "BrokerScorecard",
        "generate_demo_executions",
    ]
