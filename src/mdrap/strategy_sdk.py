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

    class _StubStrategy:
        _is_stub = True

        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "StrategySDK requires the 'mdrap_strategies' companion package. "
                "Install packages/mdrap-strategies to use algorithmic strategy execution."
            )

    class Strategy(_StubStrategy):
        pass

    class StrategyRunner:
        def __init__(self, *args, **kwargs):
            pass

    class RiskManager:
        def __init__(self, *args, **kwargs):
            pass

    class RiskLimits:
        def __init__(self, *args, **kwargs):
            pass

    class Order:
        def __init__(self, *args, **kwargs):
            pass

    class OrderBook:
        def __init__(self, *args, **kwargs):
            pass

    class OrderBookSnapshot:
        def __init__(self, *args, **kwargs):
            pass

    class OrderSide:
        def __init__(self, *args, **kwargs):
            pass

    class OrderStatus:
        def __init__(self, *args, **kwargs):
            pass

    class OrderType:
        def __init__(self, *args, **kwargs):
            pass

    class PaperExecutor:
        def __init__(self, *args, **kwargs):
            pass

    class Position:
        def __init__(self, *args, **kwargs):
            pass

    class SpreadCaptureMarketMaker(_StubStrategy):
        pass

    class WhaleMomentumStrategy(_StubStrategy):
        pass

    class AvellanedaStoikovStrategy(_StubStrategy):
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
