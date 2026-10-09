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

try:
    import mdrap_strategies as _impl
except ImportError:
    _pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "packages", "mdrap-strategies")
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    import mdrap_strategies as _impl

__stability__ = "experimental"
sys.modules[__name__] = _impl
