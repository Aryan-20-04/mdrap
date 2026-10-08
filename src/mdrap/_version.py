"""Canonical MDRAP package version and release metadata (Audit Phase 6)."""

from __future__ import annotations

import os
import subprocess

__version__ = "3.1.0"
__stability__ = "stable"
__segment_version__ = 1
__journal_version__ = 1
__stability_policy__ = (
    "Semantic Versioning 2.0.0; Core engine backwards compatibility guaranteed "
    "across minor releases; Deprecated APIs maintained for two minor versions."
)


def _detect_commit() -> str:
    """Retrieve Git commit SHA dynamically with fallback."""
    env_commit = os.environ.get("MDRAP_BUILD_COMMIT")
    if env_commit:
        return env_commit
    try:
        repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "remediated-tree"


__commit__ = _detect_commit()
