"""Tests for Gate 8 Dependency Security Audit & Classification Logic.

Verifies:
1. Scanner unavailable (Outcome C: infrastructure failure).
2. Scanner available with zero findings (Outcome A: clean pass).
3. Scanner available with findings present (Outcome B: vulnerability failure).
4. Unexpected scanner execution error (Outcome C: unexpected exit code).
5. Real execution with active Python interpreter.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch
import pytest

from scripts.check import gate_dependency_vulnerability_scan, _ARTIFACT_DIR


def test_gate8_scanner_unavailable(monkeypatch, tmp_path):
    """Test Outcome C: When scanner binary/module is unavailable, report execution failure, NOT vulnerabilities."""

    def fake_run(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="No module named pip_audit"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()

    err = str(exc_info.value)
    assert "Supply chain security audit execution failure" in err
    assert "No module named pip_audit" in err
    assert "vulnerabilities detected" not in err.lower()


def test_gate8_clean_audit(monkeypatch, tmp_path):
    """Test Outcome A: Scanner executes cleanly with 0 vulnerabilities detected and atomic report creation."""
    fake_audit_json = tmp_path / "DEPENDENCY_AUDIT.json"
    clean_data = {
        "dependencies": [
            {"name": "pytest", "version": "9.0.3", "vulns": []},
            {"name": "rich", "version": "13.9.4", "vulns": []},
        ]
    }
    captured_cmds = []

    def fake_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(json.dumps(clean_data), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    # Should execute and pass without exception
    gate_dependency_vulnerability_scan()
    assert fake_audit_json.exists()
    saved = json.loads(fake_audit_json.read_text(encoding="utf-8"))
    assert saved["scanner"]["version"] == "pip-audit 2.10.1"
    assert saved["scanner"]["interpreter"] == sys.executable
    assert saved["scanner"]["input"] == "requirements.txt"
    # Temporary -o files must be cleaned up after atomic replacement
    assert not list(tmp_path.glob(".DEPENDENCY_AUDIT*.tmp"))


def test_gate8_vulnerabilities_detected(monkeypatch, tmp_path):
    """Test Outcome B: Scanner identifies known CVE vulnerabilities, preserves full list, and fails gate."""
    fake_audit_json = tmp_path / "DEPENDENCY_AUDIT.json"
    vuln_data = {
        "dependencies": [
            {
                "name": "vulnerable-pkg",
                "version": "1.0.0",
                "vulns": [
                    {
                        "id": "GHSA-xxxx-yyyy-zzzz",
                        "fix_versions": ["1.0.1"],
                        "description": "Critical security flaw",
                    }
                ],
            }
        ]
    }

    def fake_run(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(json.dumps(vuln_data), encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Found 1 known vulnerability"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()

    err = str(exc_info.value)
    assert "pip-audit found 1 known vulnerabilities" in err
    # Vulnerability results must be preserved atomically even on nonzero CVE exit
    assert fake_audit_json.exists()
    saved = json.loads(fake_audit_json.read_text(encoding="utf-8"))
    assert saved["dependencies"][0]["vulns"][0]["id"] == "GHSA-xxxx-yyyy-zzzz"


def test_gate8_unexpected_scanner_error(monkeypatch, tmp_path):
    """Test Outcome C: Scanner crashes (e.g. inaccessible advisory DB, exit code 2) cleanly as execution failure."""

    def fake_run(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        return subprocess.CompletedProcess(
            cmd,
            2,
            stdout="",
            stderr="Fatal network timeout connecting to PyPI advisory database",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()

    err = str(exc_info.value)
    assert (
        "Supply chain security audit execution failure (unexpected scanner exit code 2)"
        in err
    )
    assert "Fatal network timeout" in err


def test_gate8_interrupted_scan_and_stale_report_rejected(monkeypatch, tmp_path):
    """Pre-existing stale clean DEPENDENCY_AUDIT.json must NEVER mask an aborted or incomplete new scan."""
    stale_audit = tmp_path / "DEPENDENCY_AUDIT.json"
    stale_payload = {
        "dependencies": [{"name": "pytest", "version": "9.0.3", "vulns": []}],
        "scanner": {"scanned_at": "2020-01-01T00:00:00+00:00"},
    }
    stale_audit.write_text(json.dumps(stale_payload), encoding="utf-8")

    # Case 1: pip-audit exits 1 without writing -o temp output (e.g. resolution failure / SIGINT)
    def fake_aborted(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="ResolutionError: conflicted"
        )

    monkeypatch.setattr(subprocess, "run", fake_aborted)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()
    assert "fresh audit output JSON missing or empty" in str(exc_info.value)
    assert "found 0 known vulnerabilities" not in str(exc_info.value)
    assert (
        json.loads(stale_audit.read_text(encoding="utf-8"))["scanner"]["scanned_at"]
        == "2020-01-01T00:00:00+00:00"
    )

    # Case 2: pip-audit exits 1 and writes 0-vuln JSON (nonzero exit mismatch) -> execution failure, not clean
    def fake_rc1_empty_vulns(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text(json.dumps({"dependencies": []}), encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Internal audit abort"
        )

    monkeypatch.setattr(subprocess, "run", fake_rc1_empty_vulns)
    with pytest.raises(RuntimeError) as exc_info2:
        gate_dependency_vulnerability_scan()
    assert "exited 1 without vulnerability findings" in str(exc_info2.value)


def test_gate8_malformed_json_rejected_and_preserves_prior_report(
    monkeypatch, tmp_path
):
    """Malformed JSON output from pip-audit must raise execution failure without corrupting prior report."""
    prior = tmp_path / "DEPENDENCY_AUDIT.json"
    prior.write_text(
        json.dumps({"dependencies": [], "sentinel": True}), encoding="utf-8"
    )

    def fake_corrupt(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text("{corrupt truncated json", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_corrupt)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()
    assert "corrupt JSON output" in str(exc_info.value)
    assert json.loads(prior.read_text(encoding="utf-8"))["sentinel"] is True


def test_gate8_isolated_audit_tool_policy_distinguishes_py310_filelock(
    monkeypatch, tmp_path
):
    """Clean requirements.txt + scanner-only filelock==4.1.0 advisory on <3.11 is recorded transparently."""
    fake_audit_json = tmp_path / "DEPENDENCY_AUDIT.json"

    def fake_run(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        out_path = Path(cmd[cmd.index("-o") + 1])
        manifest = cmd[cmd.index("-r") + 1]
        if manifest == "requirements-audit.txt":
            out_path.write_text(
                json.dumps(
                    {
                        "dependencies": [
                            {
                                "name": "filelock",
                                "version": "4.1.0",
                                "vulns": [
                                    {
                                        "id": "CVE-2026-112906",
                                        "fix_versions": ["4.2.1"],
                                        "description": "fl",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="Found 1 known vulnerability"
            )
        out_path.write_text(
            json.dumps(
                {"dependencies": [{"name": "rich", "version": "13.9.4", "vulns": []}]}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(sys, "version_info", (3, 10, 12, "final", 0))
    monkeypatch.delenv("MDRAP_STRICT_AUDIT_TOOL", raising=False)

    gate_dependency_vulnerability_scan()
    saved = json.loads(fake_audit_json.read_text(encoding="utf-8"))
    assert saved["audit_tool_environment"]["manifest"] == "requirements-audit.txt"
    assert saved["audit_tool_environment"]["shipped_to_runtime"] is False
    assert saved["audit_tool_environment"]["vulnerability_count"] == 1
    assert (
        saved["audit_tool_environment"]["vulnerabilities"][0]["id"] == "CVE-2026-112906"
    )


def test_gate8_real_execution(monkeypatch, tmp_path):
    """Test real non-mocked execution of gate_dependency_vulnerability_scan using active environment."""
    repo_artifact_dir = (
        Path(__file__).resolve().parent.parent / "audit" / "v5.3.0-remediation"
    )
    real_audit_file = repo_artifact_dir / "DEPENDENCY_AUDIT.json"
    real_before_exists = real_audit_file.exists()
    real_before_bytes = real_audit_file.read_bytes() if real_before_exists else None
    real_before_mtime_ns = (
        real_audit_file.stat().st_mtime_ns if real_before_exists else None
    )
    real_before_entries = (
        sorted(p.name for p in repo_artifact_dir.iterdir())
        if repo_artifact_dir.exists()
        else []
    )

    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    # This invokes real pip-audit against requirements.txt (and requirements-audit.txt if present)
    gate_dependency_vulnerability_scan()
    audit_file = tmp_path / "DEPENDENCY_AUDIT.json"
    assert audit_file.exists()
    assert not list(tmp_path.glob(".DEPENDENCY_AUDIT*.tmp"))
    data = json.loads(audit_file.read_text(encoding="utf-8"))
    assert "scanner" in data
    assert data["scanner"]["name"] == "pip-audit"
    assert data["scanner"]["input"] == "requirements.txt"
    assert "dependencies" in data
    assert all(
        (d.get("name") or "").lower() != "filelock" for d in data["dependencies"]
    )

    # Regression check: repository's actual artifact directory and DEPENDENCY_AUDIT.json must remain untouched
    real_after_entries = (
        sorted(p.name for p in repo_artifact_dir.iterdir())
        if repo_artifact_dir.exists()
        else []
    )
    assert real_after_entries == real_before_entries
    assert real_audit_file.exists() is real_before_exists
    if real_before_exists:
        assert real_audit_file.read_bytes() == real_before_bytes
        assert real_audit_file.stat().st_mtime_ns == real_before_mtime_ns
