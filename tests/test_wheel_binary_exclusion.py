"""
Adversarial test verifying PEP 427 pure Python wheel compliance (VULN-03).
Asserts that built pure-Python wheels do not bundle platform-specific compiled binaries.
"""

import os
import zipfile
import pytest


def test_pure_wheel_contains_no_compiled_binaries():
    """Verify built wheels in dist/ with py3-none-any tag do not leak native binaries."""
    dist_dir = "dist"
    if not os.path.exists(dist_dir):
        pytest.skip("dist/ directory not found")

    wheels = [f for f in os.listdir(dist_dir) if f.endswith(".whl") and "py3-none-any" in f]
    if not wheels:
        pytest.skip("No pure-Python wheels found in dist/")

    # Check the latest mdrap_core wheel
    core_wheels = [f for f in wheels if f.startswith("mdrap_core")]
    assert len(core_wheels) > 0, "No mdrap_core pure Python wheel found"

    latest_wheel = os.path.join(dist_dir, sorted(core_wheels)[-1])
    leaked_binaries = []

    with zipfile.ZipFile(latest_wheel, "r") as z:
        for name in z.namelist():
            if name.endswith((".dll", ".so", ".pyd", ".dylib")):
                leaked_binaries.append(name)

    assert len(leaked_binaries) == 0, (
        f"PEP 427 violation: Pure-Python wheel '{latest_wheel}' contains compiled binaries: {leaked_binaries}"
    )
