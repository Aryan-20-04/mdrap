"""Shim forwarding to mdrap.incidents."""
import importlib, sys
_mod = importlib.import_module("mdrap.incidents")
sys.modules[__name__] = _mod
