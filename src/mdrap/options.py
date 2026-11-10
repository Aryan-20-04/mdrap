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
    _pkg_init = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "packages",
        "mdrap-options",
        "mdrap_options",
        "__init__.py",
    )
    if os.path.exists(_pkg_init):
        import importlib.util

        spec = importlib.util.spec_from_file_location("mdrap_options", _pkg_init)
        if spec and spec.loader:
            try:
                _mod = importlib.util.module_from_spec(spec)
                sys.modules["mdrap_options"] = _mod
                spec.loader.exec_module(_mod)
                _impl = _mod
            except Exception:
                _impl = None

__stability__ = "experimental"

if _impl is not None:
    sys.modules[__name__] = _impl
else:
    import enum

    class CompanionPackageMissing(ImportError):
        """Raised when an optional companion package is required but not installed."""
        pass

    class OptionType(str, enum.Enum):
        CALL = "CALL"
        PUT = "PUT"

    class ExerciseStyle(str, enum.Enum):
        EUROPEAN = "EUROPEAN"
        AMERICAN = "AMERICAN"

    class OptionContract:
        def __init__(self, *args, **kwargs):
            raise CompanionPackageMissing(
                "OptionContract requires the 'mdrap_options' companion package. "
                "Install packages/mdrap-options to use options and derivatives pricing features."
            )

    class OptionsChain:
        def __init__(self, *args, **kwargs):
            raise CompanionPackageMissing(
                "OptionsChain requires the 'mdrap_options' companion package. "
                "Install packages/mdrap-options to use options and derivatives pricing features."
            )

    class BlackScholes:
        def __init__(self, *args, **kwargs):
            raise CompanionPackageMissing("BlackScholes requires 'mdrap_options'.")

    class BinomialTree:
        def __init__(self, *args, **kwargs):
            raise CompanionPackageMissing("BinomialTree requires 'mdrap_options'.")

    class Greeks:
        pass

    class OptionPrice:
        pass

    def price_option(*args, **kwargs):
        raise CompanionPackageMissing(
            "price_option requires the 'mdrap_options' companion package. "
            "Install packages/mdrap-options to use options and derivatives pricing features."
        )

    __all__ = [
        "CompanionPackageMissing",
        "OptionType",
        "ExerciseStyle",
        "OptionContract",
        "OptionsChain",
        "BlackScholes",
        "BinomialTree",
        "Greeks",
        "OptionPrice",
        "price_option",
    ]
