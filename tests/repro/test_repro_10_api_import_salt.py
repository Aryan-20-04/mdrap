"""Repro 10: Verify that importing `mdrap.api` does not fail when no production salt or demo mode is set.

Expected behavior:
Importing `mdrap.api` is a safe library import that never crashes on missing runtime secrets.
Application instantiation is deferred to `create_app(...)`.
Production startup still fails closed if secrets are missing when the server actually starts.

Current defect:
`mdrap/api.py` line 1190 executes `app = create_app()` at module top level.
`AppState` constructs `SecurityManager(store=self.store)`.
Outside demo mode without MDRAP_API_KEY_SALT, `SecurityManager.__init__` raises:
`ValueError: MDRAP_API_KEY_SALT must be set to a non-empty value outside demo mode`
Crashing any import of `mdrap.api`!
"""

from __future__ import annotations

import subprocess
import sys
import pytest


@pytest.mark.xfail(
    strict=True,
    reason="Importing mdrap.api crashes with ValueError outside demo mode because app=create_app() runs at module top level (Finding 10)",
)
def test_import_mdrap_api_without_salt_or_demo_does_not_crash():
    # Run in an isolated subprocess with clean environment (no MDRAP_DEMO, no MDRAP_API_KEY_SALT)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.environ.pop('MDRAP_DEMO', None); os.environ.pop('MDRAP_API_KEY_SALT', None); import mdrap.api; print('IMPORT_SUCCESS')",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        f"Importing mdrap.api failed with exit code {proc.returncode}!\n"
        f"Stdout: {proc.stdout}\nStderr: {proc.stderr}"
    )
    assert "IMPORT_SUCCESS" in proc.stdout
