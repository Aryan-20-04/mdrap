"""MDRAP vessel intelligence module — delegates to companion package mdrap_vessel."""

from __future__ import annotations

import os
import sys
import warnings

warnings.warn(
    "VesselTracker and AIS vessel intelligence in mdrap.vessel are deprecated and non-core "
    "to the market data engine; use mdrap_vessel companion package instead.",
    DeprecationWarning,
    stacklevel=2,
)

try:
    import mdrap_vessel as _impl
except ImportError:
    _pkg = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "packages", "mdrap-contrib-vessel")
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    import mdrap_vessel as _impl

__stability__ = "experimental"
sys.modules[__name__] = _impl
