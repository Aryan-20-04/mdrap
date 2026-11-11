import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("mdrap.protocols")
_sys.modules[__name__] = _module
