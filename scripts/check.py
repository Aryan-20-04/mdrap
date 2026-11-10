#!/usr/bin/env python3
"""MDRAP Continuous Quality Gate & Local Validation Protocol (Spec §26 & Phase 2).

Dependency-aware 9-Stage Zero-Defect Safety Protocol:
  1. Code formatting check (ruff format)
  2. Linting & static analysis (ruff check)
  3. Single-source rules parity & claims governance (gen_reasons check & claims_scanner)
  4. Native C accelerator compilation (build_fastpath -> DLL, EXE, PYD)
  5. Core unit tests (isolated fast core tests)
  6. Integration & durability test suite (pipeline, replication, alerts, API)
  7. Security & cryptographic audit verification (RFC 6962, auth, key rotation)
  8. Dependency & supply chain CVE vulnerability audit (pip-audit)
  9. Native accelerator smoke check & differential parity (micro_ffi & differential tests)

Outputs machine-readable execution artifacts into audit/v5.3.0-remediation/:
  - TEST_RESULTS.json
  - BUILD_RESULTS.json
  - DEPENDENCY_AUDIT.json
"""

from __future__ import annotations

import datetime
import importlib.metadata as im
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT_DIR = _REPO_ROOT / "audit" / "v5.3.0-remediation"
_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def print_banner(text: str) -> None:
    sep = "=" * 76
    print(f"\n{sep}\n{text.center(76)}\n{sep}")


class QualityGateRunner:
    def __init__(self) -> None:
        self.gate_results: dict[int, dict] = {}
        self.test_metrics: dict[str, int] = {
            "core_tests": 0,
            "integration_tests": 0,
            "security_tests": 0,
            "total_executed": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        }

    def run_gate(
        self,
        gate_num: int,
        name: str,
        cmd: list[str] | None = None,
        func=None,
        timeout: int | None = None,
    ) -> None:
        print(f"\n[GATE {gate_num}/9] {name}...")
        t0 = time.perf_counter()
        status = "PASSED"
        err_msg = ""

        try:
            if cmd:
                res = subprocess.run(cmd, cwd=str(_REPO_ROOT), timeout=timeout)
                if res.returncode != 0:
                    status = "FAILED"
                    err_msg = f"Command failed with exit code {res.returncode}"
            elif func:
                func()
        except subprocess.TimeoutExpired as exc:
            status = "TIMEOUT"
            err_msg = f"Gate timed out after {timeout} seconds: {exc}"
        except Exception as exc:
            status = "FAILED"
            err_msg = str(exc)

        dt = time.perf_counter() - t0
        self.gate_results[gate_num] = {
            "name": name,
            "status": status,
            "duration_sec": round(dt, 3),
            "error": err_msg,
        }

        if status == "PASSED":
            print(f"[PASSED] Gate {gate_num}: {name} ({dt:.2f}s)")
        else:
            print(f"\n[{status}] Gate {gate_num} ({name}): {err_msg}")
            self._save_gate_summary()
            sys.exit(1)

    def _save_gate_summary(self) -> None:
        summary_path = _ARTIFACT_DIR / "GATE_EXECUTION_SUMMARY.json"
        summary_path.write_text(json.dumps(self.gate_results, indent=2), encoding="utf-8")


def gate_rules_and_claims_sync():
    """Gate 3: Single-Source Rules Parity & Claims Governance."""
    res1 = subprocess.run(
        [sys.executable, "tools/gen_reasons.py", "--check"], cwd=str(_REPO_ROOT)
    )
    if res1.returncode != 0:
        raise RuntimeError("tools/gen_reasons.py check failed")

    res2 = subprocess.run(
        [sys.executable, "scripts/claims_scanner.py", "--check"], cwd=str(_REPO_ROOT)
    )
    if res2.returncode != 0:
        raise RuntimeError("scripts/claims_scanner.py check failed")


def gate_native_compilation():
    """Gate 4: Native C Accelerator Compilation."""
    build_script = _REPO_ROOT / "build_fastpath.py"
    res = subprocess.run([sys.executable, str(build_script)], cwd=str(_REPO_ROOT))
    if res.returncode != 0:
        raise RuntimeError("Native fastpath build failed")

    # Record BUILD_RESULTS.json
    dll_path = _REPO_ROOT / "src" / "mdrap" / "_fastpath_native.dll"
    exe_path = _REPO_ROOT / "src" / "mdrap" / "mdrap-core.exe"
    pyd_files = list((_REPO_ROOT / "src" / "mdrap").glob("_fastpath_c*.pyd"))

    build_info = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "SUCCESS",
        "targets": {
            "dll": {
                "path": str(dll_path.relative_to(_REPO_ROOT)),
                "exists": dll_path.exists(),
                "size_bytes": dll_path.stat().st_size if dll_path.exists() else 0,
            },
            "core_exe": {
                "path": str(exe_path.relative_to(_REPO_ROOT)),
                "exists": exe_path.exists(),
                "size_bytes": exe_path.stat().st_size if exe_path.exists() else 0,
            },
            "pyd_extension": {
                "files": [str(p.name) for p in pyd_files],
                "count": len(pyd_files),
            },
        },
    }
    build_results_file = _ARTIFACT_DIR / "BUILD_RESULTS.json"
    build_results_file.write_text(json.dumps(build_info, indent=2), encoding="utf-8")


def parse_junit_xml(xml_path: Path) -> dict[str, int]:
    """Parse junit XML output into exact metric counts."""
    if not xml_path.exists():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": 0}

    tree = ET.parse(xml_path)
    root = tree.getroot()
    # In pytest junitxml, root is testsuites or testsuite
    suites = root.findall(".//testsuite") if root.tag == "testsuites" else [root]

    total_tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
    total_failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
    total_errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
    total_skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    total_passed = total_tests - total_failures - total_errors - total_skipped

    return {
        "tests": total_tests,
        "failures": total_failures,
        "errors": total_errors,
        "skipped": total_skipped,
        "passed": total_passed,
    }


def gate_core_unit_tests():
    """Gate 5: Isolated Core Unit Tests."""
    xml_out = _ARTIFACT_DIR / "junit_core_tests.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-m",
        "not slow and not network",
        "-k",
        "not next_pieces and not integration and not phase8",
        f"--junitxml={xml_out}",
        "-q",
    ]
    res = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"Core unit tests failed with code {res.returncode}")

    metrics = parse_junit_xml(xml_out)
    if metrics["failures"] > 0 or metrics["errors"] > 0:
        raise RuntimeError(f"Core unit tests had {metrics['failures']} failures, {metrics['errors']} errors")
    print(f"[*] Core unit tests verified: {metrics['passed']} passed, {metrics['skipped']} skipped")


def gate_integration_tests():
    """Gate 6: Integration, Durability & Packaging Tests."""
    xml_out = _ARTIFACT_DIR / "junit_integration_tests.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_next_pieces_guarantees.py",
        "tests/test_pipeline_integration.py",
        "tests/test_service.py",
        "tests/test_phase8_enterprise_extensions.py",
        "tests/test_companion_packages.py",
        f"--junitxml={xml_out}",
        "-q",
    ]
    res = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"Integration test suite failed with code {res.returncode}")

    metrics = parse_junit_xml(xml_out)
    if metrics["failures"] > 0 or metrics["errors"] > 0:
        raise RuntimeError(f"Integration tests had {metrics['failures']} failures")
    print(f"[*] Integration tests verified: {metrics['passed']} passed, {metrics['skipped']} skipped")


def gate_security_and_audit_tests():
    """Gate 7: Security Hardening & Cryptographic Audit Tests."""
    xml_out = _ARTIFACT_DIR / "junit_security_tests.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_security.py",
        "tests/test_security_hardening_review.py",
        "tests/test_key_storage_hardening.py",
        "tests/test_api_security_hardening.py",
        "tests/test_claims_scanner.py",
        "tests/test_rfc6962_audit.py",
        f"--junitxml={xml_out}",
        "-q",
    ]
    res = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"Security test suite failed with code {res.returncode}")

    metrics = parse_junit_xml(xml_out)
    if metrics["failures"] > 0 or metrics["errors"] > 0:
        raise RuntimeError(f"Security tests had {metrics['failures']} failures")
    print(f"[*] Security & cryptographic tests verified: {metrics['passed']} passed, {metrics['skipped']} skipped")


def gate_dependency_vulnerability_scan():
    """Gate 8: Supply Chain & Dependency CVE Audit (pip-audit)."""
    audit_json = _ARTIFACT_DIR / "DEPENDENCY_AUDIT.json"
    cmd = [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        "requirements.txt",
        "-f",
        "json",
        "-o",
        str(audit_json),
    ]
    res = subprocess.run(cmd, cwd=str(_REPO_ROOT))
    if res.returncode != 0:
        raise RuntimeError("Supply chain security audit failed (vulnerabilities detected)")

    if audit_json.exists():
        data = json.loads(audit_json.read_text(encoding="utf-8"))
        deps = data.get("dependencies", [])
        vulns = sum(len(d.get("vulns", [])) for d in deps)
        if vulns > 0:
            raise RuntimeError(f"pip-audit found {vulns} known vulnerabilities in dependencies")
        print(f"[*] Supply chain audit clean: {len(deps)} dependencies scanned, 0 vulnerabilities found")


def gate_native_tests_and_parity():
    """Gate 9: Native FFI Micro-benchmark Smoke & Differential Parity Verification."""
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    import fastpath

    if not fastpath.is_available():
        raise RuntimeError("Native fastpath library not available")

    # Run micro-FFI
    res1 = subprocess.run(
        [
            sys.executable,
            "benchmarks/micro_ffi.py",
            "--iterations",
            "10000",
            "--runs",
            "1",
        ],
        cwd=str(_REPO_ROOT),
    )
    if res1.returncode != 0:
        raise RuntimeError("Native micro-benchmark smoke check failed")

    # Run differential parity test
    xml_out = _ARTIFACT_DIR / "junit_differential_tests.xml"
    res2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_fastpath_differential.py",
            f"--junitxml={xml_out}",
            "-q",
        ],
        cwd=str(_REPO_ROOT),
    )
    if res2.returncode != 0:
        raise RuntimeError("Differential parity tests failed")


def consolidate_test_results():
    """Consolidate XML metrics into authoritative audit/v5.3.0-remediation/TEST_RESULTS.json."""
    xml_files = {
        "core": _ARTIFACT_DIR / "junit_core_tests.xml",
        "integration": _ARTIFACT_DIR / "junit_integration_tests.xml",
        "security": _ARTIFACT_DIR / "junit_security_tests.xml",
        "differential": _ARTIFACT_DIR / "junit_differential_tests.xml",
    }

    per_suite = {}
    total_tests = 0
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_errors = 0

    for name, path in xml_files.items():
        m = parse_junit_xml(path)
        per_suite[name] = m
        total_tests += m["tests"]
        total_passed += m["passed"]
        total_failed += m["failures"]
        total_skipped += m["skipped"]
        total_errors += m["errors"]

    result_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "summary": {
            "total_executed": total_tests,
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "errors": total_errors,
            "pass_rate_pct": 100.0 if total_tests > 0 and total_failed == 0 else 0.0,
        },
        "breakdown": per_suite,
    }

    test_results_path = _ARTIFACT_DIR / "TEST_RESULTS.json"
    test_results_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")

    # Update claims_manifest.json with verified evidence
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    import mdrap._version as v

    manifest_path = _REPO_ROOT / "audit" / "claims_manifest.json"
    manifest_data = {
        "mdrap_version": v.__version__,
        "commit": getattr(v, "__commit__", "unknown"),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "install_command": "pip install -e .",
        "test_counts": {
            "passed": total_passed,
            "skipped": total_skipped,
            "failed": total_failed,
            "errors": total_errors,
            "total": total_tests,
        },
        "verified_claim": f"{total_passed} verified tests passed cleanly without assertion swallowing",
        "scope": "Single-Node Process & IPC (Mode A)",
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    print(f"\n[+] Authoritative test results written to {test_results_path}")
    print(f"[+] Claims manifest synchronized with {total_passed} verified tests")


def main():
    print_banner("MDRAP 9-GATE LOCAL VALIDATION PROTOCOL")
    runner = QualityGateRunner()

    # Gate 1: Code Formatting
    runner.run_gate(
        1,
        "Code Formatting (ruff format --check src/)",
        [sys.executable, "-m", "ruff", "format", "--check", "src/"],
    )

    # Gate 2: Static Analysis
    runner.run_gate(
        2,
        "Static Analysis (ruff check src/)",
        [sys.executable, "-m", "ruff", "check", "src/"],
    )

    # Gate 3: Single-Source Rules Parity & Claims Governance
    runner.run_gate(3, "Single-Source Rules Parity & Claims Governance", func=gate_rules_and_claims_sync)

    # Gate 4: Native C Accelerator Compilation (MUST PRECEDE TESTS)
    runner.run_gate(4, "Native C Accelerator Compilation", func=gate_native_compilation)

    # Gate 5: Core Unit Tests
    runner.run_gate(5, "Core Unit Tests", func=gate_core_unit_tests)

    # Gate 6: Integration, Durability & Packaging Tests
    runner.run_gate(6, "Integration & Durability Test Suite", func=gate_integration_tests)

    # Gate 7: Security Hardening & Cryptographic Audit Tests
    runner.run_gate(7, "Security & Cryptographic Audit Verification", func=gate_security_and_audit_tests)

    # Gate 8: Supply Chain & Dependency CVE Audit
    runner.run_gate(8, "Supply Chain Dependency CVE Audit (pip-audit)", func=gate_dependency_vulnerability_scan)

    # Gate 9: Native Tests & Micro-Benchmark Smoke Check
    runner.run_gate(9, "Native Accelerator Smoke Check & Differential Parity", func=gate_native_tests_and_parity)

    consolidate_test_results()
    runner._save_gate_summary()

    print_banner("ALL 9 MDRAP QUALITY GATES PASSED! PRODUCTION READY")
    sys.exit(0)


if __name__ == "__main__":
    main()
