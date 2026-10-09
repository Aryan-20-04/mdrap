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

try:
    import mdrap_analytics as _impl
except ImportError:
    _pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "packages", "mdrap-analytics")
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    import mdrap_analytics as _impl

__stability__ = "experimental"
sys.modules[__name__] = _impl
