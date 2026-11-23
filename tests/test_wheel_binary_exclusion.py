"""
Adversarial test verifying PEP 427 pure Python wheel compliance (VULN-03).
Asserts that built pure-Python wheels do not bundle platform-specific compiled binaries.
"""

import os
import pathlib
import subprocess
import sys
import zipfile
from email.parser import Parser


def test_pure_wheel_contains_no_compiled_binaries(tmp_path):
    """Build and inspect a fresh universal wheel in the test's isolated temp dir."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    wheel_dir = tmp_path / "wheelhouse"
    wheel_dir.mkdir()
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"build --wheel failed with exit code {res.returncode}.\n"
            f"Working directory: {repo_root}\n"
            f"Python executable: {sys.executable}\n"
            f"--- BUILD STDOUT ---\n{res.stdout}\n"
            f"--- BUILD STDERR ---\n{res.stderr}"
        )

    from mdrap._version import __version__
    wheels = sorted(wheel_dir.glob("mdrap_core-*.whl"))
    assert len(wheels) == 1, f"Expected one fresh core wheel, found {wheels}"
    latest_wheel = wheels[0]
    assert latest_wheel.name == f"mdrap_core-{__version__}-py3-none-any.whl"
    leaked_binaries = []

    with zipfile.ZipFile(latest_wheel, "r") as z:
        metadata_path = f"mdrap_core-{__version__}.dist-info/METADATA"
        wheel_path = f"mdrap_core-{__version__}.dist-info/WHEEL"
        metadata = Parser().parsestr(z.read(metadata_path).decode("utf-8"))
        wheel_metadata = z.read(wheel_path).decode("utf-8")
        assert metadata["Version"] == __version__
        assert "Tag: py3-none-any" in wheel_metadata
        for name in z.namelist():
            if name.lower().endswith((".dll", ".so", ".pyd", ".dylib")):
                leaked_binaries.append(name)

    assert len(leaked_binaries) == 0, (
        f"PEP 427 violation: Pure-Python wheel '{latest_wheel}' contains compiled binaries: {leaked_binaries}"
    )
