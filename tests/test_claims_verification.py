"""Claim Verification & Universal Quality Gate Tests (C-01 .. C-05).

Ensures documentation claims, version strings, architectural boundaries,
and generated test manifests are synchronized and truthful.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_c04_version_consistency():
    """Verify version is synchronized across code, configurations, and docs."""
    from mdrap._version import __version__

    assert __version__ == "6.0.8"

    compat_toml = (REPO_ROOT / "compat" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "6.0.8"' in compat_toml

    contrib_toml = (REPO_ROOT / "contrib" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "6.0.8"' in contrib_toml

    cargo_toml = (REPO_ROOT / "sdk" / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    assert (
        'version = "4.0.0"' in cargo_toml
        or 'version = "4.0.1"' in cargo_toml
        or 'version = "5.3.0"' in cargo_toml
    )


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
    """Verify consensus module is completely pruned from read-only core."""
    with pytest.raises(ModuleNotFoundError):
        import mdrap.consensus  # noqa: F401


def test_x01_call_site_review_record():
    """Verify zero eval, exec, pickle, shell=True, and non-test assert in src/ (X-01)."""
    disallowed_patterns = {
        "eval": re.compile(r"\beval\s*\("),
        "exec": re.compile(r"\bexec\s*\("),
        "pickle": re.compile(r"\bpickle\b"),
        "shell_true": re.compile(r"shell\s*=\s*True"),
        "assert": re.compile(r"^\s*assert\b"),
    }
    src_dir = REPO_ROOT / "src"
    violations = []
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(content.splitlines(), 1):
            for pat_name, pattern in disallowed_patterns.items():
                if pattern.search(line):
                    violations.append(
                        f"{py_file.name}:{line_no} [{pat_name}]: {line.strip()}"
                    )

    assert not violations, f"Found disallowed call sites in src/: {violations}"


def _prepare_isolated_claims_fixture(tmp_path: Path, manifest_data: dict) -> Path:
    """Create an isolated mini repo fixture for testing scripts/gen_claims.py --check."""
    tests_dir = tmp_path / "tests"
    docs_dir = tmp_path / "docs"
    audit_dir = tmp_path / "audit"
    tests_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    (tests_dir / "test_sample.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n\n"
        "def test_two():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    (docs_dir / "status.md").write_text(
        "| **Mode A** | Isolated Fixture | 0+ core test suite passed cleanly | Verified |\n",
        encoding="utf-8",
    )
    manifest_path = audit_dir / "claims_manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    # Pin an older deterministic mtime so any rewrite or touch is detectable
    os.utime(manifest_path, (1_700_000_000, 1_700_000_000))
    return manifest_path


def _expected_fixture_manifest() -> dict:
    from mdrap._version import __version__

    return {
        "mdrap_version": __version__,
        "commit": "unknown",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "install_command": "pip install -e .",
        "test_counts": {
            "passed": 2,
            "skipped": 0,
            "deselected": 0,
            "failed": 0,
            "total": 2,
        },
        "verified_claim": "0+ core test suite passed cleanly",
        "scope": "Single-Node Process & IPC (Mode A)",
    }


def _snapshot_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run_gen_claims_check_cli(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "gen_claims.py"),
            "--check",
            "--repo-root",
            str(tmp_path),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def test_gen_claims_check_leaves_manifest_bytes_and_mtime_unchanged(tmp_path):
    """A: gen_claims.py --check must not modify or touch an existing manifest."""
    manifest_path = _prepare_isolated_claims_fixture(
        tmp_path, _expected_fixture_manifest()
    )
    before_bytes = manifest_path.read_bytes()
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    res = _run_gen_claims_check_cli(tmp_path)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert manifest_path.read_bytes() == before_bytes
    assert manifest_path.stat().st_mtime_ns == before_mtime_ns


def test_gen_claims_check_mismatch_fails_without_modifying_manifest(tmp_path):
    """B: Mismatched manifest fails nonzero in --check mode without repairing or rewriting."""
    mismatched = _expected_fixture_manifest()
    mismatched["test_counts"]["passed"] = 999
    mismatched["test_counts"]["total"] = 999
    mismatched["verified_claim"] = "950+ core test suite passed cleanly"

    manifest_path = _prepare_isolated_claims_fixture(tmp_path, mismatched)
    before_bytes = manifest_path.read_bytes()
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    res = _run_gen_claims_check_cli(tmp_path)
    assert res.returncode != 0
    combined = f"{res.stdout}\n{res.stderr}"
    assert "mismatch" in combined.lower()
    assert manifest_path.read_bytes() == before_bytes
    assert manifest_path.stat().st_mtime_ns == before_mtime_ns


def test_gen_claims_check_matching_manifest_succeeds_without_rewrite(tmp_path):
    """C: Valid manifest with distinct formatting and older timestamp passes cleanly unchanged."""
    manifest = _expected_fixture_manifest()
    manifest_path = _prepare_isolated_claims_fixture(tmp_path, manifest)
    # Write compact non-pretty JSON to ensure --check never normalizes formatting
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    os.utime(manifest_path, (1_700_000_123, 1_700_000_123))
    before_bytes = manifest_path.read_bytes()
    before_mtime_ns = manifest_path.stat().st_mtime_ns

    res = _run_gen_claims_check_cli(tmp_path)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "(read-only)" in res.stdout
    assert manifest_path.read_bytes() == before_bytes
    assert manifest_path.stat().st_mtime_ns == before_mtime_ns


def test_gen_claims_check_creates_no_unintended_files(tmp_path):
    """D: Check mode creates zero extra files, temp manifests, or reports in fixture root."""
    _prepare_isolated_claims_fixture(tmp_path, _expected_fixture_manifest())
    before_tree = _snapshot_tree(tmp_path)

    res_match = _run_gen_claims_check_cli(tmp_path)
    assert res_match.returncode == 0, f"{res_match.stdout}\n{res_match.stderr}"
    assert _snapshot_tree(tmp_path) == before_tree

    # Also verify missing manifest fails without creating audit/claims_manifest.json or temp files
    manifest_path = tmp_path / "audit" / "claims_manifest.json"
    manifest_path.unlink()
    before_missing_tree = _snapshot_tree(tmp_path)

    res_missing = _run_gen_claims_check_cli(tmp_path)
    assert res_missing.returncode != 0
    assert _snapshot_tree(tmp_path) == before_missing_tree
