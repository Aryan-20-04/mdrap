"""
MDRAP CLI Entry Point Shim
Forwards execution to src/cli.py so that both direct execution (`python cli.py`)
and package execution (`from cli import main`) work seamlessly.
"""
import sys
import os
import importlib.util

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_cli_path = os.path.join(_SRC, "cli.py")
_spec = importlib.util.spec_from_file_location("_src_cli", _cli_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

for _k, _v in _mod.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

main = _mod.main

if __name__ == "__main__":
    main()
