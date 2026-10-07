import importlib
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_module = importlib.import_module("mdrap.build_fastpath")

if __name__ == "__main__":
    out_target = (
        sys.argv[1]
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
        else None
    )
    ok1 = _module.build(target_dir=out_target, quiet=False)
    ok2 = _module.build_core(target_dir=out_target, quiet=False)
    ok3 = _module.build_extension(target_dir=out_target, quiet=False)
    sys.exit(0 if (ok1 and ok2) else 1)
else:
    sys.modules[__name__] = _module
