import importlib as _importlib
import sys as _sys
import warnings as _warnings

_warnings.warn(
    "Importing from root 'journal' is deprecated in MDRAP v3.0.0; "
    "use 'mdrap.ingestlog' for WAL durability instead.",
    DeprecationWarning,
    stacklevel=2,
)

_module = _importlib.import_module("mdrap.journal")
_sys.modules[__name__] = _module
