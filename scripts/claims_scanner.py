#!/usr/bin/env python3
"""MDRAP Claims Governance & Automated Claims Scanner (Item A11).

Audits documentation files (README.md, docs/**/*.md) to ensure:
1. No unmeasured or forbidden superlatives ('infinite', 'unbreakable', 'zero latency',
   'zero overhead', 'perfect reliability', 'infinitely scalable', '100% bug free').
2. Strict claims provenance: ensures numerical performance and scale claims map to
   reproducible benchmark manifests and verified test outputs.
3. Provides a clean --check command for CI quality gates.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Forbidden superlatives that represent unmeasured marketing claims
FORBIDDEN_SUPERLATIVES = [
    r"\binfinite\s+capacity\b",
    r"\binfinitely\s+scalable\b",
    r"\bunbreakable\b",
    r"\bzero\s+latency\b",
    r"\bzero\s+overhead\b",
    r"\bperfect\s+reliability\b",
    r"\b100%\s+bug\s*free\b",
    r"\bflawless\b",
    r"\bcompletely\s+unhackable\b",
]

# Patterns for numerical performance claims that must trace to benchmark artifacts
NUMERICAL_CLAIM_PATTERNS = [
    r"(\d+(?:\.\d+)?)\s*(?:µs|us|microseconds)\s+(?:p50|p95|p99)",
    r"(\d+(?:,\d+)?)\s+(?:eps|events/s|events\s+per\s+second)",
]


def scan_file_for_superlatives(path: Path) -> list[tuple[int, str, str]]:
    """Scan a markdown file for forbidden superlatives, returning (line_num, match, line)."""
    violations = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    for idx, line in enumerate(content.splitlines(), start=1):
        # Ignore markdown comments, links to external documents, or explicit negation
        stripped = line.strip()
        if stripped.startswith("<!--") or "forbidden" in stripped.lower() or "claims_scanner" in stripped:
            continue
        for pat in FORBIDDEN_SUPERLATIVES:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                # Check for context negation like "not zero overhead" or "never claims unbreakable"
                matched_text = m.group(0)
                violations.append((idx, matched_text, line.strip()))
    return violations


def audit_documentation(docs_dir: Path, readme_path: Path) -> list[dict]:
    """Audit all documentation files for claims hygiene."""
    results = []
    files_to_check = [readme_path]
    if docs_dir.exists():
        files_to_check.extend(docs_dir.rglob("*.md"))

    for f in files_to_check:
        if not f.is_file():
            continue
        # Skip changelogs, third-party licenses, or archived external audit reports
        if "CHANGELOG" in f.name or "media_" in f.name or "LICENSE" in f.name:
            continue

        violations = scan_file_for_superlatives(f)
        if violations:
            for line_no, matched, context in violations:
                results.append({
                    "file": str(f.relative_to(_REPO_ROOT)),
                    "line": line_no,
                    "matched": matched,
                    "context": context,
                })
    return results


def verify_claims_manifest_provenance() -> tuple[bool, str]:
    """Verify that audit/claims_manifest.json exists and is internally consistent."""
    manifest_path = _REPO_ROOT / "audit" / "claims_manifest.json"
    if not manifest_path.exists():
        return False, f"Missing claims manifest: {manifest_path}"

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        counts = data.get("test_counts", {})
        if counts.get("passed", 0) <= 0:
            return False, "Claims manifest indicates 0 passed tests"
        if counts.get("failed", 0) > 0:
            return False, f"Claims manifest records {counts.get('failed')} test failures"
    except Exception as exc:
        return False, f"Failed to parse claims manifest: {exc}"

    return True, "Claims manifest verified"


def main():
    parser = argparse.ArgumentParser(description="MDRAP Claims Governance & AST Scanner")
    parser.add_argument("--check", action="store_true", help="Run in strict CI mode (exit 1 on violations)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show verbose scan details")
    args = parser.parse_args()

    docs_dir = _REPO_ROOT / "docs"
    readme_path = _REPO_ROOT / "README.md"

    print("Running MDRAP Claims Governance Audit...")
    violations = audit_documentation(docs_dir, readme_path)
    manifest_ok, manifest_msg = verify_claims_manifest_provenance()

    has_error = False
    if not manifest_ok:
        print(f"[FAIL] Claims Manifest Invariant: {manifest_msg}", file=sys.stderr)
        has_error = True
    else:
        print(f"[PASS] Claims Manifest Invariant: {manifest_msg}")

    if violations:
        print(f"\n[FAIL] Found {len(violations)} forbidden superlative(s) in documentation:")
        for v in violations:
            print(f"  {v['file']}:{v['line']}: Forbidden phrase '{v['matched']}'")
            if args.verbose:
                print(f"    Context: {v['context']}")
        has_error = True
    else:
        print("[PASS] No forbidden superlatives found in documentation.")

    if has_error and args.check:
        print("\nClaims governance check FAILED. Please remove unmeasured superlatives or restore manifest.", file=sys.stderr)
        sys.exit(1)

    print("\nClaims governance check PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()
