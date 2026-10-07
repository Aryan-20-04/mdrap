import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("mdrap.adapters")
for _name in ("reference", "template"):
    _sys.modules[f"{__name__}.{_name}"] = _importlib.import_module(
        f"mdrap.adapters.{_name}"
    )
_sys.modules[__name__] = _module
