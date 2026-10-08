"""
MDRAP CLI Entry Point Shim
Forwards execution to mdrap.cli so that direct execution (`python cli.py`)
and package execution (`from cli import main`) work seamlessly.
"""
import sys
import os
import importlib

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_module = importlib.import_module("mdrap.cli")
sys.modules[__name__] = _module

if __name__ == "__main__":
    _module.main()

