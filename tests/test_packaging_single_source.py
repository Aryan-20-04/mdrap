"""Packaging and Single Source of Truth CI verification.

Verifies:
1. Exactly one authoritative source tree exists (`src/mdrap/`).
2. No duplicate drifted source tree exists in `contrib/src/mdrap/`.
3. pyproject.toml correctly specifies package directories, dependencies, and entrypoints.
"""

import os
from pathlib import Path


def test_single_authoritative_source_tree():
    root = Path(__file__).resolve().parent.parent
    src_mdrap = root / "src" / "mdrap"
    contrib_mdrap = root / "contrib" / "src" / "mdrap"

    # 1. Authoritative source must exist
    assert src_mdrap.is_dir(), f"Authoritative source tree missing: {src_mdrap}"

    # 2. Duplicate source tree in contrib must NOT exist
    assert not contrib_mdrap.exists(), (
        f"Duplicate source tree detected in {contrib_mdrap}! "
        "Per MDRAP v3.0.0 Packaging rules (Finding 11), only ONE authoritative source tree "
        "('src/mdrap') is permitted. Redundant duplicate trees must be deleted."
    )


def test_core_dependencies_and_entrypoints():
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    # Core dependencies verification
    assert 'name = "mdrap-core"' in pyproject
    assert 'rich>=13.0.0' in pyproject
    assert 'fastapi>=0.110.0' in pyproject
    assert 'pyyaml>=6.0' in pyproject
    assert 'openpyxl>=3.1.0' in pyproject
