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
    """Test Outcome A: Scanner executes cleanly with 0 vulnerabilities detected."""
    fake_audit_json = tmp_path / "DEPENDENCY_AUDIT.json"
    clean_data = {
        "dependencies": [
            {"name": "pytest", "version": "9.0.3", "vulns": []},
            {"name": "rich", "version": "13.9.4", "vulns": []},
        ]
    }

    def fake_run(cmd, *args, **kwargs):
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="pip-audit 2.10.1\n", stderr=""
            )
        # Write output file
        fake_audit_json.write_text(json.dumps(clean_data), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    # Should execute and pass without exception
    gate_dependency_vulnerability_scan()
    assert fake_audit_json.exists()
    saved = json.loads(fake_audit_json.read_text(encoding="utf-8"))
    assert saved["scanner"]["version"] == "pip-audit 2.10.1"
    assert saved["scanner"]["interpreter"] == sys.executable


def test_gate8_vulnerabilities_detected(monkeypatch, tmp_path):
    """Test Outcome B: Scanner identifies known CVE vulnerabilities, correctly formats findings and fails."""
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
        fake_audit_json.write_text(json.dumps(vuln_data), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("scripts.check._ARTIFACT_DIR", tmp_path)

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()

    err = str(exc_info.value)
    assert "pip-audit found 1 known vulnerabilities" in err


def test_gate8_unexpected_scanner_error(monkeypatch, tmp_path):
    """Test Outcome C: Scanner crashes with unexpected nonzero return code (e.g. exit code 2 on network error)."""

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

    with pytest.raises(RuntimeError) as exc_info:
        gate_dependency_vulnerability_scan()

    err = str(exc_info.value)
    assert (
        "Supply chain security audit execution failure (unexpected scanner exit code 2)"
        in err
    )
    assert "Fatal network timeout" in err


def test_gate8_real_execution():
    """Test real non-mocked execution of gate_dependency_vulnerability_scan using active environment."""
    # This invokes real pip-audit in the local Python environment
    gate_dependency_vulnerability_scan()
    audit_file = _ARTIFACT_DIR / "DEPENDENCY_AUDIT.json"
    assert audit_file.exists()
    data = json.loads(audit_file.read_text(encoding="utf-8"))
    assert "scanner" in data
    assert data["scanner"]["name"] == "pip-audit"
    assert "dependencies" in data
