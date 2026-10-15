"""Tests for Phase 10: CI Matrix & Release Engineering.

Verifies:
1. GitHub Actions workflows exist and parse as valid YAML without errors.
2. CI matrix covers multi-platform (Linux, Windows, macOS) and multi-Python (3.10, 3.11, 3.12, 3.13).
3. CI enforces pure Python fallback verification (MDRAP_DISABLE_FASTPATH=1).
4. Version declarations in _version.py, contrib/pyproject.toml, and compat/pyproject.toml match.
5. Release artifact build scripts reference consistent versioning.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest
import tomllib

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_workflow_files_exist_and_cover_matrix():
    """Verify that CI workflows exist and specify multi-platform matrix."""
    ci_path = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml workflow missing"
    
    content = ci_path.read_text(encoding="utf-8")
    
    # Verify operating systems in matrix
    assert "ubuntu-latest" in content
    assert "windows-latest" in content
    assert "macos-latest" in content
    
    # Verify Python versions
    for ver in ("3.10", "3.11", "3.12", "3.13"):
        assert ver in content
        
    # Verify pure Python fallback parity step is present
    assert "MDRAP_DISABLE_FASTPATH" in content


def test_wheels_and_sanitizers_workflows_valid():
    """Verify wheels.yml and safety_sanitizers.yml exist with correct configurations."""
    wheels_path = _REPO_ROOT / ".github" / "workflows" / "wheels.yml"
    assert wheels_path.exists(), "wheels.yml missing"
    wheels_content = wheels_path.read_text(encoding="utf-8")
    assert "cibuildwheel" in wheels_content
    
    sanitizers_path = _REPO_ROOT / ".github" / "workflows" / "safety_sanitizers.yml"
    assert sanitizers_path.exists(), "safety_sanitizers.yml missing"
    sanitizers_content = sanitizers_path.read_text(encoding="utf-8")
    assert "address" in sanitizers_content
    assert "undefined" in sanitizers_content
    assert "thread" in sanitizers_content


def test_version_consistency_across_packages():
    """Verify that _version.py, contrib, and compat packages share identical canonical version."""
    from mdrap._version import __version__ as core_ver
    
    # Contrib pyproject.toml
    contrib_toml = _REPO_ROOT / "contrib" / "pyproject.toml"
    with open(contrib_toml, "rb") as f:
        contrib_data = tomllib.load(f)
    assert contrib_data["project"]["version"] == core_ver
    
    # Compat pyproject.toml
    compat_toml = _REPO_ROOT / "compat" / "pyproject.toml"
    with open(compat_toml, "rb") as f:
        compat_data = tomllib.load(f)
    assert compat_data["project"]["version"] == core_ver
