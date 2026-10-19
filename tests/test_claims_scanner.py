"""Tests for Claims Governance Scanner (Item A11).

Validates:
1. Claims scanner correctly catches forbidden superlatives.
2. Docs and README are free of unmeasured superlatives.
3. Claims manifest is present and valid.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.claims_scanner import (
    FORBIDDEN_SUPERLATIVES,
    audit_documentation,
    scan_file_for_superlatives,
    verify_claims_manifest_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_claims_manifest_is_valid():
    """Verify that audit/claims_manifest.json exists and has valid counts."""
    ok, msg = verify_claims_manifest_provenance()
    assert ok, f"Claims manifest validation failed: {msg}"


def test_documentation_free_of_forbidden_superlatives():
    """Ensure no docs contain unmeasured superlatives."""
    docs_dir = REPO_ROOT / "docs"
    readme = REPO_ROOT / "README.md"
    violations = audit_documentation(docs_dir, readme)
    assert not violations, f"Found claims violations: {violations}"


def test_scanner_detects_deliberate_violation(tmp_path):
    """Test scanner catches bad words on a dummy document."""
    bad_doc = tmp_path / "bad.md"
    bad_doc.write_text(
        "MDRAP offers unbreakable security and zero latency trading.", encoding="utf-8"
    )
    violations = scan_file_for_superlatives(bad_doc)
    assert len(violations) >= 2
    matched = [v[1].lower() for v in violations]
    assert "unbreakable" in matched
    assert "zero latency" in matched


def test_claims_scanner_cli_invocable():
    """Verify scripts/claims_scanner.py can be invoked via CLI with exit 0."""
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "claims_scanner.py"), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Scanner CLI failed: {res.stdout}\n{res.stderr}"
