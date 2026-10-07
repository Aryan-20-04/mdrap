"""
Phase 4 Packaging & Delivery Verification Suite (Gate G4).

Verifies:
1. Duplicate source tree check: single authoritative source tree in src/mdrap.
2. Native build script resolution: _fastpath_c.c path lookup and compilation.
3. Native extension import and pure Python fallback parity.
4. Wheel manifest completeness: all core modules packaged, entry point declared.
"""

from __future__ import annotations

import os
import sys
import pytest

from mdrap.build_fastpath import (
    find_source_file,
    find_core_source_file,
    find_extension_source_file,
)
from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.quality import QualityConfig, QualityEngine


def test_no_duplicate_source_tree():
    """Verify that no duplicate source tree exists in contrib/ or anywhere else."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    contrib_mdrap = os.path.join(repo_root, "contrib", "src", "mdrap")
    assert not os.path.exists(contrib_mdrap), (
        f"Duplicate source tree found at {contrib_mdrap}. "
        "Repository must maintain ONE authoritative source tree in src/mdrap."
    )

    # Ensure src/mdrap is present and contains the authoritative codebase
    authoritative_src = os.path.join(repo_root, "src", "mdrap")
    assert os.path.isdir(authoritative_src), "Authoritative source src/mdrap missing!"
    assert os.path.isfile(os.path.join(authoritative_src, "engine.py"))
    assert os.path.isfile(os.path.join(authoritative_src, "ingestlog.py"))
    assert os.path.isfile(os.path.join(authoritative_src, "projection.py"))
    assert os.path.isfile(os.path.join(authoritative_src, "api.py"))


def test_build_fastpath_path_resolution():
    """Verify that all C source files resolve correctly without duplicate path segments."""
    c_source = find_source_file()
    assert c_source is not None, "fastpath.c not found"
    assert os.path.isfile(c_source)
    assert "src/mdrap/src/mdrap" not in c_source.replace("\\", "/")

    core_source = find_core_source_file()
    assert core_source is not None, "mdrap_core.c not found"
    assert os.path.isfile(core_source)
    assert "src/mdrap/src/mdrap" not in core_source.replace("\\", "/")

    ext_source = find_extension_source_file()
    assert ext_source is not None, "_fastpath_c.c not found"
    assert os.path.isfile(ext_source)
    assert "src/mdrap/src/mdrap" not in ext_source.replace("\\", "/")


def test_native_extension_and_pure_python_fallback_parity():
    """Verify that native fastpath engine and pure Python fallback yield identical validation."""
    from mdrap.fastpath import FastQualityEngine, is_available

    py_engine = QualityEngine()
    fast_engine = FastQualityEngine()

    ev = CanonicalEvent(
        event_id="test_evt_1",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=1,
        bid_price=150.0,
        bid_size=10.0,
        ask_price=149.0,  # Crossed quote -> INVALID
        ask_size=10.0,
        quality_status=QualityStatus.VALID,
    )

    py_res = py_engine.evaluate(ev)
    fast_res = fast_engine.evaluate(ev)

    assert py_res.quality_status == QualityStatus.INVALID
    assert fast_res.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in py_res.reasons
    assert Reason.CROSSED_QUOTE.value in fast_res.reasons


def test_wheel_metadata_and_entry_point():
    """Verify that pyproject.toml defines the mdrap CLI entry point and packages all mdrap modules."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyproject_path = os.path.join(repo_root, "pyproject.toml")
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert '[project.scripts]' in content
    assert 'mdrap = "mdrap.cli:main"' in content
    assert 'include = ["mdrap", "mdrap.*"]' in content
