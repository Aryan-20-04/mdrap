"""
Pytest configuration for MDRAP test suite.
"""

import os
import sys
import pytest

# Ensure all companion packages under packages/ are importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGES_DIR = os.path.join(_REPO_ROOT, "packages")
if os.path.isdir(_PACKAGES_DIR):
    for entry in os.listdir(_PACKAGES_DIR):
        pkg_path = os.path.join(_PACKAGES_DIR, entry)
        if os.path.isdir(pkg_path) and pkg_path not in sys.path:
            sys.path.insert(0, pkg_path)

# Tests use a stable non-demo salt; production must configure its own value.
os.environ.setdefault("MDRAP_API_KEY_SALT", "test-only-mdrap-salt-not-for-deployment")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Enforce MDRAP_REQUIRE_NATIVE=1 in CI, failing tests that skip on missing native acceleration (MDRAP-TEST-02)."""
    outcome = yield
    rep = outcome.get_result()
    if rep.skipped and os.environ.get("MDRAP_REQUIRE_NATIVE") == "1":
        longrepr = str(getattr(rep, "longrepr", ""))
        combined = longrepr.lower()
        if any(
            kw in combined
            for kw in ("native", "fastpath", "shm", "c-extension", "compiled")
        ):
            rep.outcome = "failed"
            rep.failed = True
            rep.skipped = False
            rep.longrepr = (
                f"MDRAP_REQUIRE_NATIVE=1: Test skipped due to missing native acceleration! "
                f"Skip reason was: {longrepr}"
            )
