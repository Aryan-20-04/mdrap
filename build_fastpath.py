import importlib.util
import os
import sys

_SRC_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "src", "mdrap", "build_fastpath.py"
)
_spec = importlib.util.spec_from_file_location("mdrap_build_fastpath", _SRC_FILE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load build_fastpath from {_SRC_FILE}")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

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
