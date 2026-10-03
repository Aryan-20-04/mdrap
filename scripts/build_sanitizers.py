"""
Script to build MDRAP native hot-path components with sanitizers (ASan, UBSan, TSan).
Supports Linux (GCC/Clang) and macOS, with graceful fallback checks on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def build_with_sanitizer(sanitizer_type: str = "address", target_dir: str = "build") -> bool:
    os.makedirs(target_dir, exist_ok=True)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_c = os.path.join(repo_root, "src", "fastpath.c")
    core_c = os.path.join(repo_root, "src", "mdrap_core.c")

    cc = os.environ.get("CC", "gcc")
    if not shutil.which(cc):
        print(f"[sanitizers] Compiler {cc} not found in PATH.")
        return False

    out_so = os.path.join(target_dir, f"libfastpath_{sanitizer_type}.so")
    out_core = os.path.join(target_dir, f"mdrap_core_{sanitizer_type}")

    flags = [
        f"-fsanitize={sanitizer_type}",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-Wall",
        "-Wextra",
        "-D_GNU_SOURCE",
    ]

    print(f"[sanitizers] Compiling fastpath with -fsanitize={sanitizer_type}...")
    cmd_so = [cc, *flags, "-shared", "-fPIC", "-o", out_so, src_c, "-lm"]
    res_so = subprocess.run(cmd_so, capture_output=True, text=True)
    if res_so.returncode != 0:
        print(f"[sanitizers] Failed to build {out_so}:\n{res_so.stderr}")
        return False

    print(f"[sanitizers] Compiling mdrap_core with -fsanitize={sanitizer_type}...")
    cmd_core = [cc, *flags, "-o", out_core, core_c, "-lm"]
    res_core = subprocess.run(cmd_core, capture_output=True, text=True)
    if res_core.returncode != 0:
        print(f"[sanitizers] Failed to build {out_core}:\n{res_core.stderr}")
        return False

    print(f"[sanitizers] Successfully built sanitizer targets for: {sanitizer_type}")
    return True


if __name__ == "__main__":
    san = sys.argv[1] if len(sys.argv) > 1 else "address"
    success = build_with_sanitizer(san)
    sys.exit(0 if success else 1)
