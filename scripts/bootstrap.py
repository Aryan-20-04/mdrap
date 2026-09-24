#!/usr/bin/env python3
"""MDRAP Clean-Room Bootstrap & Build Script (Phase 26).

Enables 1-command fresh build from a bare git clone:
  1. Python runtime validation (>= 3.10)
  2. Dependency installation & editable packaging (pip install -e ".[all]")
  3. Native C accelerator compilation (build_fastpath.py)
  4. Rules single-source of truth check (gen_reasons.py)
  5. Fast verification smoke test suite

Usage:
  python scripts/bootstrap.py [--no-deps]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def print_step(step_num: int, total_steps: int, title: str) -> None:
    print(f"\n[{step_num}/{total_steps}] {title}...")


def run_command(cmd: list[str], err_msg: str) -> None:
    t0 = time.perf_counter()
    res = subprocess.run(cmd, cwd=_REPO_ROOT)
    if res.returncode != 0:
        print(
            f"\n[BOOTSTRAP ERROR] {err_msg} (exit code {res.returncode})",
            file=sys.stderr,
        )
        sys.exit(res.returncode)
    dt = time.perf_counter() - t0
    print(f"    Completed in {dt:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="MDRAP Clean-Room Bootstrap")
    parser.add_argument(
        "--no-deps", action="store_true", help="Skip pip dependency installation"
    )
    args = parser.parse_args()

    print("=" * 72)
    print("MDRAP CLEAN-ROOM BOOTSTRAP & SYSTEM BUILD".center(72))
    print("=" * 72)

    total_steps = 4 if args.no_deps else 5
    step = 1

    # Step 1: Runtime validation
    print_step(step, total_steps, "Validating Python Runtime")
    step += 1
    if sys.version_info < (3, 10):
        print(
            f"[ERROR] MDRAP requires Python >= 3.10, found {sys.version}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"    Detected Python: {sys.version.split()[0]} on {sys.platform}")

    # Step 2: Install dependencies
    if not args.no_deps:
        print_step(step, total_steps, "Installing Dependencies & Editable Package")
        step += 1
        run_command(
            [sys.executable, "-m", "pip", "install", "-e", ".[all]"],
            "Failed installing project dependencies via pip",
        )

    # Step 3: Compile native accelerators
    print_step(step, total_steps, "Compiling Native C Hot-Path Accelerators")
    step += 1
    run_command(
        [sys.executable, "build_fastpath.py"],
        "Native C compilation failed",
    )

    # Step 4: Rules parity check
    print_step(step, total_steps, "Verifying Single-Source Rules Parity (rules.def)")
    step += 1
    run_command(
        [sys.executable, "tools/gen_reasons.py", "--check"],
        "Single-source rules.def parity check failed",
    )

    # Step 5: Smoke test suite
    print_step(step, total_steps, "Executing Smoke Verification Test Suite")
    run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_pipeline_integration.py",
            "tests/test_canonical_models.py",
            "tests/test_docs_verification.py",
            "-q",
        ],
        "Smoke tests failed",
    )

    print("\n" + "=" * 72)
    print("BOOTSTRAP SUCCEEDED! MDRAP IS FULLY OPERATIONAL".center(72))
    print("=" * 72)
    print("\nNext steps:")
    print("  Run full quality gates:   python scripts/check.py")
    print("  Run benchmarks:          python benchmarks/run_performance_suite.py")
    print("  Start live terminal:     python cli.py")
    print()


if __name__ == "__main__":
    main()
