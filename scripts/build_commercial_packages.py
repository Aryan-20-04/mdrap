#!/usr/bin/env python3
"""Build and packaging automation for MDRAP commercial companion extensions.

Builds standalone wheel packages for the commercial/companion suite:
- mdrap-options
- mdrap-analytics
- mdrap-strategies
- mdrap-contrib-vessel

Places output artifacts into dist/packages/ and verifies that commercial files
remain strictly excluded from git tracking.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PACKAGES_DIR = ROOT / "packages"
DIST_DIR = ROOT / "dist" / "packages"


def verify_gitignore_exclusion() -> bool:
    """Verify that packages/ and dist/ are safely excluded in .gitignore."""
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        return False
    content = gitignore.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    has_pkg = any("packages" in line and not line.startswith("#") for line in lines)
    has_dist = any("dist" in line and not line.startswith("#") for line in lines)
    return has_pkg and has_dist


def build_package(pkg_path: Path) -> Path | None:
    """Build a standalone wheel for a companion package."""
    print(f"\n[*] Building companion package: {pkg_path.name}...")
    dist_target = DIST_DIR
    dist_target.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        str(pkg_path),
        "--no-deps",
        "-w",
        str(dist_target),
    ]

    res = subprocess.run(cmd, cwd=str(pkg_path), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[-] Build failed for {pkg_path.name}:\n{res.stderr}")
        return None

    # Find the generated wheel
    wheels = list(dist_target.glob(f"{pkg_path.name.replace('-', '_')}*.whl"))
    if not wheels:
        # Check standard name pattern
        wheels = list(dist_target.glob("*.whl"))

    print(f"[+] Successfully built wheel in {dist_target}")
    return dist_target


def main() -> int:
    print("=" * 65)
    print("      MDRAP COMMERCIAL SUITE WHEEL PACKAGING BUILDER")
    print("=" * 65)

    if not PACKAGES_DIR.exists():
        print(f"[-] packages/ directory not found at {PACKAGES_DIR}. Skipping.")
        return 0

    if not verify_gitignore_exclusion():
        print("[!] WARNING: packages/ or dist/ is NOT safely excluded in .gitignore!")
        return 1

    companion_dirs = [d for d in PACKAGES_DIR.iterdir() if d.is_dir() and (d / "pyproject.toml").exists()]
    print(f"[*] Found {len(companion_dirs)} companion packages to build:")
    for d in companion_dirs:
        print(f"    - {d.name}")

    built_count = 0
    for d in companion_dirs:
        if build_package(d):
            built_count += 1

    print("\n" + "=" * 65)
    print(f"[+] Commercial packaging complete: {built_count}/{len(companion_dirs)} wheels built.")
    print(f"[+] Output directory: {DIST_DIR}")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
