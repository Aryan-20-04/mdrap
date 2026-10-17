"""MDRAP options module — delegates to companion package mdrap_options."""

from __future__ import annotations

import os
import sys
import warnings

warnings.warn(
    "Derivatives pricing and OptionsChain in mdrap.options are deprecated and non-core "
    "to the market data engine; use mdrap_options companion package instead.",
    DeprecationWarning,
    stacklevel=2,
)

_impl = None
try:
    import mdrap_options as _impl
except ImportError:
    _pkg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "packages",
        "mdrap-options",
    )
    if _pkg not in sys.path and os.path.exists(_pkg):
        sys.path.insert(0, _pkg)
    try:
        import mdrap_options as _impl
    except ImportError:
        _impl = None

__stability__ = "experimental"

if _impl is not None:
    sys.modules[__name__] = _impl
else:

    class OptionsChain:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "OptionsChain requires the 'mdrap_options' companion package. "
                "Install packages/mdrap-options to use options and derivatives pricing features."
            )

    class BlackScholes:
        pass

    class BinomialTree:
        pass

    __all__ = ["OptionsChain", "BlackScholes", "BinomialTree"]
