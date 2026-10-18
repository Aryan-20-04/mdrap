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

_impl = None
if os.environ.get("MDRAP_DISABLE_COMPANIONS") != "1":
    try:
        import mdrap_vessel as _impl
    except ImportError:
        _pkg = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "packages",
            "mdrap-contrib-vessel",
        )
        if _pkg not in sys.path and os.path.exists(_pkg):
            sys.path.insert(0, _pkg)
        try:
            import mdrap_vessel as _impl
        except ImportError:
            _impl = None

__stability__ = "experimental"

if _impl is not None:
    sys.modules[__name__] = _impl
else:

    class VesselTracker:
        def __init__(self, *args, **kwargs):
            from .exceptions import CompanionPackageMissing

            raise CompanionPackageMissing(
                "mdrap_vessel",
                "pip install packages/mdrap-contrib-vessel",
            )

    class Vessel:
        pass

    GLOBAL_CHOKEPOINTS: dict = {}

    def haversine_nm(lat1, lon1, lat2, lon2):
        import math

        r_nm = 3440.065
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        )
        return 2 * r_nm * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    __all__ = ["VesselTracker", "Vessel", "GLOBAL_CHOKEPOINTS", "haversine_nm"]
