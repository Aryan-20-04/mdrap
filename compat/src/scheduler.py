import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("mdrap.scheduler")
_sys.modules[__name__] = _module
