import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("mdrap._version")
_sys.modules[__name__] = _module
