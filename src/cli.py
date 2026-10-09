import importlib as _importlib
import sys as _sys

_module = _importlib.import_module("mdrap.cli")
_sys.modules[__name__] = _module

if __name__ == "__main__":
    _module.main()

