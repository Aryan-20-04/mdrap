#!/usr/bin/env python3
"""MDRAP Documentation Verification & Quality Guardrail (tools/check_docs.py).

Verifies that:
1. All python code blocks annotated with `# doc-test` run without errors.
2. README.md version badge matches pyproject.toml / mdrap._version.
3. Performance figures in README.md match committed benchmarks/*.json files.
4. All relative markdown links in README.md and docs/ point to existing files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"


def check_version() -> bool:
    """Verify README.md version badge matches canonical package version."""
    version_file = _SRC_DIR / "mdrap" / "_version.py"
    if not version_file.exists():
        print(f"[FAIL] Version file not found at {version_file}")
        return False

    ns: dict[str, str] = {}
    with open(version_file, "r", encoding="utf-8") as f:
        exec(f.read(), ns)
    canonical_version = ns.get("__version__", "")
    if not canonical_version:
        print("[FAIL] Could not extract __version__ from _version.py")
        return False

    readme_path = _REPO_ROOT / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")

    # Match badge e.g. badge/version-4.0.0-blue.svg
    m = re.search(r"badge/version-([0-9a-zA-Z\.\-]+)-blue\.svg", readme_text)
    if not m:
        print("[FAIL] Could not find version badge in README.md")
        return False

    badge_version = m.group(1)
    if badge_version != canonical_version:
        print(
            f"[FAIL] README version badge ({badge_version}) != canonical version ({canonical_version})"
        )
        return False

    print(f"[PASS] Version badge matches: {canonical_version}")
    return True


def check_doc_tests() -> bool:
    """Find and execute all '# doc-test' code blocks in README.md and docs/."""
    all_md_files = [
        _REPO_ROOT / "README.md",
        *(_REPO_ROOT / "docs").rglob("*.md"),
    ]

    all_passed = True
    tested_count = 0

    for md_file in all_md_files:
        content = md_file.read_text(encoding="utf-8")
        # Match python code blocks with # doc-test
        code_blocks = re.findall(r"```python\s*(# doc-test[\s\S]*?)```", content)
        for idx, block in enumerate(code_blocks, 1):
            tested_count += 1
            rel_path = md_file.relative_to(_REPO_ROOT)
            print(f"  Running doc-test #{idx} in {rel_path}...", end=" ", flush=True)

            env = {**os.environ, "PYTHONPATH": str(_SRC_DIR)}
            proc = subprocess.run(
                [sys.executable, "-c", block],
                cwd=_REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                print("FAILED!")
                print(f"[STDERR]\n{proc.stderr}")
                all_passed = False
            else:
                print("OK")

    print(f"[PASS] Executed {tested_count} doc-tests successfully.")
    return all_passed


def check_benchmark_sync() -> bool:
    """Verify performance metrics in README.md match committed JSON results."""
    stage_json = _REPO_ROOT / "benchmarks" / "stage_breakdown.json"
    honest_json = _REPO_ROOT / "benchmarks" / "results" / "honest_benchmarks.json"

    if not stage_json.exists() or not honest_json.exists():
        print("[FAIL] Missing benchmark JSON files.")
        return False

    with open(stage_json, "r", encoding="utf-8") as f:
        stage_data = json.load(f)
    with open(honest_json, "r", encoding="utf-8") as f:
        honest_data = json.load(f)

    c_kernel_eps = int(
        stage_data["scheduling_execution"]["native_c_kernel"]["eps"]
    )
    python_p50 = honest_data["engine_step"]["latencies_us"]["p50"]
    fanout_10_eps = int(
        honest_data["fanout"]["10_clients"]["publish_throughput_eps"]
    )

    readme_text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    # Format numbers as expected in README table
    c_kernel_str = f"{c_kernel_eps:,}"
    fanout_10_str = f"{fanout_10_eps:,}"

    if c_kernel_str not in readme_text:
        print(f"[FAIL] Native C kernel EPS ({c_kernel_str}) missing from README.md")
        return False
    if fanout_10_str not in readme_text:
        print(f"[FAIL] Fanout 10-client EPS ({fanout_10_str}) missing from README.md")
        return False
    if f"{python_p50} µs" not in readme_text:
        print(f"[FAIL] Python p50 latency ({python_p50} µs) missing from README.md")
        return False

    print(
        f"[PASS] Benchmark numbers in README match committed JSON (Native C: {c_kernel_str} EPS, Fanout 10: {fanout_10_str} EPS, Python: {python_p50} µs)."
    )
    return True


def check_relative_links() -> bool:
    """Verify that all relative markdown links in README.md and docs/ point to existing files."""
    all_md_files = [
        _REPO_ROOT / "README.md",
        *(_REPO_ROOT / "docs").rglob("*.md"),
    ]

    all_passed = True
    checked_links = 0

    for md_file in all_md_files:
        content = md_file.read_text(encoding="utf-8")
        links = re.findall(r"\[.*?\]\((?!http|https|#|mailto:)(.*?)\)", content)
        for link in links:
            # Strip anchor if present
            clean_link = link.split("#")[0].strip()
            if not clean_link:
                continue
            checked_links += 1
            # Resolve relative to current md file
            target = (md_file.parent / unquote(clean_link)).resolve()
            # Also allow resolution relative to repo root
            root_target = (_REPO_ROOT / unquote(clean_link)).resolve()
            # Also check if target exists under src/mdrap
            src_mdrap_target = (
                _REPO_ROOT / unquote(clean_link).replace("src/", "src/mdrap/")
            ).resolve()

            if (
                not target.exists()
                and not root_target.exists()
                and not src_mdrap_target.exists()
            ):
                rel_md = md_file.relative_to(_REPO_ROOT)
                print(f"[FAIL] Broken link in {rel_md}: {link}")
                all_passed = False

    print(f"[PASS] Verified {checked_links} relative links.")
    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(description="MDRAP Documentation Verification Guardrail")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running doc-tests")
    args = parser.parse_args()

    print("==========================================")
    print("MDRAP Documentation Verification Guardrail")
    print("==========================================")

    v_ok = check_version()
    b_ok = check_benchmark_sync()
    l_ok = check_relative_links()
    t_ok = True if args.skip_tests else check_doc_tests()

    if v_ok and b_ok and l_ok and t_ok:
        print("\nAll documentation guardrail checks passed cleanly!")
        return 0
    else:
        print("\n[ERROR] Documentation guardrail checks failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
