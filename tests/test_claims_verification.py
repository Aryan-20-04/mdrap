"""Claim Verification & Universal Quality Gate Tests (C-01 .. C-05).

Ensures documentation claims, version strings, architectural boundaries,
and generated test manifests are synchronized and truthful.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_c04_version_consistency():
    """Verify version 3.1.2 is synchronized across code, configurations, and docs."""
    from mdrap._version import __version__

    assert __version__ == "3.1.2"

    compat_toml = (REPO_ROOT / "compat" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "3.1.2"' in compat_toml

    contrib_toml = (REPO_ROOT / "contrib" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "3.1.2"' in contrib_toml

    cargo_toml = (REPO_ROOT / "sdk" / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    assert 'version = "3.1.2"' in cargo_toml


def test_c01_readme_and_manifest_alignment():
    """Verify README Mode A test count claim matches audit/claims_manifest.json."""
    manifest_file = REPO_ROOT / "audit" / "claims_manifest.json"
    if not manifest_file.exists():
        pytest.skip("audit/claims_manifest.json not yet generated")

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    verified_claim = manifest["verified_claim"]

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert verified_claim in readme, (
        f"README.md does not contain verified manifest claim: '{verified_claim}'"
    )


def test_c02_forbidden_claims_scan():
    """Scan documentation to ensure no unqualified distributed HA / consensus claims exist."""
    forbidden_unqualified = [
        re.compile(r"\bmulti-datacenter active-active\b", re.IGNORECASE),
        re.compile(r"\bbyzantine fault tolerant\b", re.IGNORECASE),
        re.compile(r"\bzero-loss distributed failover\b", re.IGNORECASE),
    ]

    docs_to_check = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "status.md",
        REPO_ROOT / "docs" / "product" / "scope-and-guarantees.md",
    ]

    for doc_path in docs_to_check:
        if not doc_path.exists():
            continue
        text = doc_path.read_text(encoding="utf-8")
        for pattern in forbidden_unqualified:
            match = pattern.search(text)
            assert not match, (
                f"Found unqualified forbidden claim '{match.group(0)}' in {doc_path}"
            )


def test_c05_single_node_consensus_disclosure():
    """Verify ConsensusCoordinator docstring explicitly discloses single-process in-memory simulation."""
    from mdrap.consensus import ConsensusCoordinator

    doc = ConsensusCoordinator.__doc__ or ""
    assert (
        "single-process" in doc.lower()
        or "simulation" in doc.lower()
        or "in-process" in doc.lower()
    ), "ConsensusCoordinator docstring must disclose single-process/simulated nature"
