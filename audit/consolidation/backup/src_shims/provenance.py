"""Shim forwarding to mdrap.provenance."""
import importlib, sys
_mod = importlib.import_module("mdrap.provenance")
sys.modules[__name__] = _mod
