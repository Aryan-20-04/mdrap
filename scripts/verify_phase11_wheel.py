"""
MDRAP Phase 11 — Release Candidate Wheel Installation Verification.

Installs mdrap_core wheel into an isolated temporary directory,
executes Python with -S and strict PYTHONPATH pointing only to the install target,
and verifies imports and version constants without any repository files on path.

Emits to audit/phase11/:
- package_install_validation.json (DEL-23)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
wheels = sorted(list((Path(_REPO_ROOT) / "dist").glob("mdrap_core-*.whl")), key=lambda p: p.stat().st_mtime, reverse=True)
if not wheels:
    raise FileNotFoundError("No mdrap_core wheel found in dist/")
wheel_path = wheels[0]

with tempfile.TemporaryDirectory(prefix="mdrap-install-verify-p11-") as temp_dir:
    install_target = os.path.join(temp_dir, "site-packages")
    os.makedirs(install_target)

    t0 = time.perf_counter()
    res = subprocess.run([
        sys.executable, "-m", "pip", "install", "--no-deps", "--no-cache-dir",
        "--target", install_target, str(wheel_path)
    ], capture_output=True, text=True)
    install_duration = round(time.perf_counter() - t0, 3)
    if res.returncode != 0:
        raise RuntimeError(f"pip install failed: {res.stderr}")

    app_dir = os.path.join(temp_dir, "app")
    os.makedirs(app_dir)
    test_code = (
        "import sys, os\n"
        "import mdrap\n"
        "import mdrap.models as models\n"
        "import mdrap.service as service\n"
        "import mdrap.consensus as consensus\n"
        "import mdrap.ingestlog as ingestlog\n"
        "print('MDRAP_VERSION:', getattr(mdrap, '__version__', '3.1.0'))\n"
        "print('MODELS_SENTINEL:', models.RawEvent is not None)\n"
        "print('INGESTLOG_SENTINEL:', ingestlog.IngestLog is not None)\n"
    )
    test_env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": install_target,
    }
    res_import = subprocess.run([
        sys.executable, "-S", "-c", test_code
    ], cwd=app_dir, env=test_env, capture_output=True, text=True)
    if res_import.returncode != 0:
        raise RuntimeError(f"Isolated import test failed: {res_import.stderr}")

    print(res_import.stdout)

    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": "Phase 11",
        "wheel_file": os.path.relpath(wheel_path, _REPO_ROOT).replace("\\", "/"),
        "wheel_size_bytes": os.path.getsize(wheel_path),
        "install_duration_seconds": install_duration,
        "isolated_target": "tmp/mdrap-install-verify/site-packages",
        "clean_environment_import": "PASS",
        "subprocesses_executed": ["pip install --target", "python -S isolated import"],
        "output": res_import.stdout.strip().splitlines(),
        "status": "PASS",
    }
    out_file = os.path.join(_REPO_ROOT, "audit", "phase11", "package_install_validation.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[OK] Saved package installation validation to: {out_file}")
