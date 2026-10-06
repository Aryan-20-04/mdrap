import glob
import os
import pytest

VALID_STABILITIES = {"stable", "beta", "experimental"}


def test_all_src_modules_declare_valid_stability_contract():
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    py_files = sorted(glob.glob(os.path.join(src_dir, "*.py")))

    missing = []
    invalid = []

    for path in py_files:
        base = os.path.basename(path)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Look for __stability__ = "..."
        found = False
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("__stability__") and "=" in line:
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val not in VALID_STABILITIES:
                    invalid.append((base, val))
                found = True
                break

        if not found:
            missing.append(base)

    assert not missing, f"Modules missing __stability__ contract: {missing}"
    assert not invalid, f"Modules with invalid __stability__ values: {invalid}"
