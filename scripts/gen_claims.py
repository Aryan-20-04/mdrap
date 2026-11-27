#!/usr/bin/env python3
"""MDRAP Verifiable Claims & Test Manifest Generator (Phase A / Universal Plan).

Executes the baseline core test suite and generates an immutable, reproducible
claims record in audit/claims_manifest.json. Verifies that documentation claims
in README.md and docs/status.md trace to actual test outcomes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_git_commit(repo_root: str | os.PathLike[str] = _REPO_ROOT) -> str:
    root_path = Path(repo_root)
    if not (root_path / ".git").exists():
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def run_test_count(
    repo_root: str | os.PathLike[str] = _REPO_ROOT, check_only: bool = False
) -> dict[str, Any]:
    """Execute pytest on the baseline suite and extract exact passed/skipped/deselected counts."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-m",
        "not slow and not network",
        "-q",
        "--tb=short",
    ]
    env = None
    if check_only:
        cmd.extend(["-p", "no:cacheprovider"])
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    res = subprocess.run(
        cmd, cwd=str(repo_root), capture_output=True, text=True, env=env
    )
    out = res.stdout + "\n" + res.stderr

    # Parse pytest summary line: e.g. "1404 passed, 25 skipped, 58 deselected"
    counts: dict[str, Any] = {
        "passed": 0,
        "skipped": 0,
        "deselected": 0,
        "failed": 0,
        "total": 0,
    }
    last_line = ""
    for line in out.splitlines():
        line = line.strip()
        if "passed" in line or "failed" in line:
            last_line = line

    passed_m = re.search(r"(\d+)\s+passed", last_line)
    if passed_m:
        counts["passed"] = int(passed_m.group(1))

    skipped_m = re.search(r"(\d+)\s+skipped", last_line)
    if skipped_m:
        counts["skipped"] = int(skipped_m.group(1))

    deselected_m = re.search(r"(\d+)\s+deselected", last_line)
    if deselected_m:
        counts["deselected"] = int(deselected_m.group(1))

    failed_m = re.search(r"(\d+)\s+failed", last_line)
    if failed_m:
        counts["failed"] = int(failed_m.group(1))

    counts["total"] = counts["passed"] + counts["skipped"] + counts["failed"]
    counts["_raw_output"] = out
    return counts


def build_claims_manifest(
    version: str,
    commit: str,
    counts: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Calculate expected claims manifest content in memory without filesystem I/O."""
    passed = int(counts["passed"])
    rounded_baseline = (passed // 50) * 50
    claim_str = f"{rounded_baseline:,}+ core test suite passed cleanly"
    clean_counts = {
        key: value for key, value in counts.items() if not str(key).startswith("_")
    }
    return {
        "mdrap_version": version,
        "commit": commit,
        "generated_at": generated_at
        or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "install_command": "pip install -e .",
        "test_counts": clean_counts,
        "verified_claim": claim_str,
        "scope": "Single-Node Process & IPC (Mode A)",
    }


def compare_manifests(expected: dict[str, Any], existing: dict[str, Any]) -> list[str]:
    """Compare expected in-memory claims manifest against parsed existing manifest."""
    mismatches: list[str] = []
    if not isinstance(existing, dict):
        return ["Existing manifest root JSON value must be an object"]

    expected_keys = set(expected.keys())
    existing_keys = set(existing.keys())
    for missing in sorted(expected_keys - existing_keys):
        mismatches.append(f"Missing manifest field: {missing}")
    for extra in sorted(existing_keys - expected_keys):
        mismatches.append(f"Unexpected manifest field: {extra}")

    generated_at = existing.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        mismatches.append("Field 'generated_at' must be a non-empty timestamp string")

    for field in (
        "mdrap_version",
        "commit",
        "install_command",
        "verified_claim",
        "scope",
    ):
        if existing.get(field) != expected.get(field):
            mismatches.append(
                f"Field '{field}' mismatch: expected {expected.get(field)!r}, found {existing.get(field)!r}"
            )

    expected_counts = expected.get("test_counts")
    existing_counts = existing.get("test_counts")
    if not isinstance(existing_counts, dict):
        mismatches.append(
            f"Field 'test_counts' must be a mapping, found {type(existing_counts).__name__}"
        )
    elif existing_counts != expected_counts:
        if isinstance(expected_counts, dict):
            all_count_keys = sorted(
                set(expected_counts.keys()) | set(existing_counts.keys())
            )
            for key in all_count_keys:
                if existing_counts.get(key) != expected_counts.get(key):
                    mismatches.append(
                        f"Field 'test_counts.{key}' mismatch: expected {expected_counts.get(key)!r}, found {existing_counts.get(key)!r}"
                    )
        else:
            mismatches.append("Field 'test_counts' mismatch")

    return mismatches


def _matches_doc_claim(current_claim: str, rounded_baseline: int) -> bool:
    plain_re = rf"(?<!\d){re.escape(str(rounded_baseline))}\+"
    comma_re = rf"(?<!\d){re.escape(f'{rounded_baseline:,}')}\+"
    return bool(
        re.search(plain_re, current_claim) or re.search(comma_re, current_claim)
    )


def main():
    parser = argparse.ArgumentParser(
        description="Generate and verify MDRAP test and claim manifests"
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate claims against committed docs"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update README.md and status.md with generated counts",
    )
    parser.add_argument(
        "--repo-root",
        default=_REPO_ROOT,
        help="Repository root directory containing tests/, docs/, and audit/",
    )
    parser.add_argument(
        "--manifest-path",
        default=None,
        help="Optional path to claims_manifest.json (defaults to <repo-root>/audit/claims_manifest.json)",
    )
    args = parser.parse_args()

    if args.check and args.update:
        print("[ERROR] --check and --update are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

    repo_root = os.path.abspath(args.repo_root)
    manifest_path = (
        os.path.abspath(args.manifest_path)
        if args.manifest_path
        else os.path.join(repo_root, "audit", "claims_manifest.json")
    )

    from mdrap._version import __version__

    commit = get_git_commit(repo_root)
    print(
        f"Running MDRAP test suite verification (v{__version__}, commit {commit[:8]})..."
    )
    counts = run_test_count(repo_root, check_only=args.check)
    print(
        f"Results: {counts['passed']} passed, {counts['skipped']} skipped, {counts['failed']} failed, {counts['deselected']} deselected"
    )

    raw_out = counts.pop("_raw_output", "")
    if counts["failed"] > 0:
        if raw_out:
            print(raw_out, file=sys.stderr)
        print(
            f"[ERROR] Test suite failed with {counts['failed']} failures! Cannot generate verified claims.",
            file=sys.stderr,
        )
        sys.exit(1)

    manifest = build_claims_manifest(__version__, commit, counts)
    rounded_baseline = (int(counts["passed"]) // 50) * 50
    claim_str = str(manifest["verified_claim"])

    if not args.check:
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"Saved claims manifest to: {manifest_path}")
    else:
        if not os.path.exists(manifest_path):
            print(f"[FAIL] Claims manifest not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                existing_manifest = json.load(f)
        except Exception as exc:
            print(
                f"[FAIL] Failed to parse claims manifest {manifest_path}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

        mismatches = compare_manifests(manifest, existing_manifest)
        if mismatches:
            print(
                f"[FAIL] Claims manifest mismatch against computed verification state ({manifest_path}):",
                file=sys.stderr,
            )
            for item in mismatches:
                print(f"  - {item}", file=sys.stderr)
            sys.exit(1)

    # Check or update docs/status.md or README.md
    status_path = os.path.join(repo_root, "docs", "status.md")
    readme_path = os.path.join(repo_root, "README.md")
    candidate_paths = [p for p in (status_path, readme_path) if os.path.exists(p)]

    mode_a_pattern = r"(\|\s*\*\*Mode A\*\*\s*\|\s*[^|]+\|\s*)([^|]+)(\|\s*[^|]+\|)"
    doc_content = ""
    target_path = None
    match = None
    for cand in candidate_paths:
        with open(cand, "r", encoding="utf-8") as f:
            cand_content = f.read()
        cand_match = re.search(mode_a_pattern, cand_content)
        if cand_match:
            target_path = cand
            doc_content = cand_content
            match = cand_match
            break

    if not match or target_path is None:
        print("[INFO] Mode A row not present in docs; skipping tabular row assertion.")
        print("[PASSED] Test count verified.")
        sys.exit(0)

    current_claim = match.group(2).strip()
    if args.check:
        if not _matches_doc_claim(current_claim, rounded_baseline):
            print(
                f"[FAIL] Claim mismatch in {os.path.basename(target_path)}: expected {rounded_baseline:,}+ in '{current_claim}'",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"[PASSED] Verified claims manifest for {claim_str} against {os.path.basename(target_path)} (read-only)."
        )
    elif args.update:
        new_doc = re.sub(mode_a_pattern, rf"\g<1>{claim_str} \g<3>", doc_content)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_doc)
        print(f"Updated {os.path.basename(target_path)} Mode A row to: '{claim_str}'")


if __name__ == "__main__":
    main()
