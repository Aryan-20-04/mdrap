"""MDRAP strategy SDK module — delegates to companion package mdrap_strategies."""

from __future__ import annotations

import os
import sys
import warnings

warnings.warn(
    "Strategy and algorithmic execution models in mdrap.strategy_sdk are deprecated and non-core "
    "to the market data engine; use mdrap_strategies companion package instead.",
    DeprecationWarning,
    stacklevel=2,
)

_impl = None
try:
    import mdrap_strategies as _impl
except ImportError:
    _pkg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "packages",
        "mdrap-strategies",
    )
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    try:
        import mdrap_strategies as _impl
    except ImportError:
        _impl = None

__stability__ = "experimental"

if _impl is not None:
    sys.modules[__name__] = _impl
else:

    class Strategy:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "StrategySDK requires the 'mdrap_strategies' companion package. "
                "Install packages/mdrap-strategies to use algorithmic strategy execution."
            )

    class StrategyRunner:
        def __init__(self, *args, **kwargs):
            pass

    class RiskManager:
        def __init__(self, *args, **kwargs):
            pass

    class RiskLimits:
        pass

    class Order:
        pass

    class OrderBook:
        pass

    class OrderBookSnapshot:
        pass

    class OrderSide:
        pass

    class OrderStatus:
        pass

    class OrderType:
        pass

    class PaperExecutor:
        pass

    class Position:
        pass

    class SpreadCaptureMarketMaker:
        pass

    class WhaleMomentumStrategy:
        pass

    class AvellanedaStoikovStrategy:
        pass

    __all__ = [
        "Strategy",
        "StrategyRunner",
        "RiskManager",
        "RiskLimits",
        "Order",
        "OrderBook",
        "OrderBookSnapshot",
        "OrderSide",
        "OrderStatus",
        "OrderType",
        "PaperExecutor",
        "Position",
        "SpreadCaptureMarketMaker",
        "WhaleMomentumStrategy",
        "AvellanedaStoikovStrategy",
    ]
