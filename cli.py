"""
MDRAP CLI Entry Point Shim
Forwards execution to src/cli.py so that both direct execution (`python cli.py`)
and package execution (`from cli import main`) work seamlessly.
"""
import sys
import os

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from cli import main

if __name__ == "__main__":
    main()
