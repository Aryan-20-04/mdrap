"""Tests for PEP 427 packaging compliance and native CI gate enforcement (MDRAP-PKG-01, MDRAP-TEST-02).

Verifies:
1. pyproject.toml package-data excludes platform-specific binary DLLs / shared libraries.
2. MDRAP_REQUIRE_NATIVE=1 hook intercepts native skips and converts them to actionable failures.
"""

import os
from unittest.mock import MagicMock
import pytest


def test_package_data_excludes_compiled_binaries():
    """Verify package-data in pyproject.toml excludes compiled binary libraries (MDRAP-PKG-01)."""
    pyproject_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pyproject.toml")
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Invariant: compiled shared library extensions must not be in package-data
    banned_extensions = [
        "_fastpath_native.dll",
        "_fastpath_native.so",
        "_fastpath_native.dylib",
        "_fastpath_native.pyd",
        "fastpath.dll",
        "fastpath.dylib",
    ]
    for ext in banned_extensions:
        assert ext not in content, f"Compiled binary '{ext}' found in pyproject.toml package-data!"


def test_mdrap_require_native_ci_gate_hook(monkeypatch):
    """Verify pytest conftest hook fails skipped native tests when MDRAP_REQUIRE_NATIVE=1 (MDRAP-TEST-02)."""
    from tests.conftest import pytest_runtest_makereport

    monkeypatch.setenv("MDRAP_REQUIRE_NATIVE", "1")

    # Mock an outcome report representing a skipped native test
    mock_rep = MagicMock()
    mock_rep.skipped = True
    mock_rep.failed = False
    mock_rep.outcome = "skipped"
    mock_rep.longrepr = "Skipped: Native C fastpath library not available"

    mock_generator = pytest_runtest_makereport(None, None)
    # Prime generator
    next(mock_generator)

    # Send result into generator
    mock_outcome = MagicMock()
    mock_outcome.get_result.return_value = mock_rep

    try:
        mock_generator.send(mock_outcome)
    except StopIteration:
        pass

    # Invariant: skipped native test must be transformed into a failure
    assert mock_rep.outcome == "failed"
    assert mock_rep.failed is True
    assert mock_rep.skipped is False
    assert "MDRAP_REQUIRE_NATIVE=1" in mock_rep.longrepr
