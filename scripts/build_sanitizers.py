"""
Script to build MDRAP native hot-path components with sanitizers (ASan, UBSan, TSan).
Supports Linux (GCC/Clang) and macOS, with graceful fallback checks on Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def build_with_sanitizer(
    sanitizer_type: str = "address", target_dir: str = "build"
) -> bool:
    os.makedirs(target_dir, exist_ok=True)
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_c = os.path.join(repo_root, "src", "mdrap", "fastpath.c")
    if not os.path.exists(src_c):
        src_c = os.path.join(repo_root, "src", "fastpath.c")
    core_c = os.path.join(repo_root, "src", "mdrap", "mdrap_core.c")
    if not os.path.exists(core_c):
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

    inc_dir = os.path.dirname(src_c)
    extra_libs = ["-lws2_32"] if sys.platform == "win32" else []

    print(f"[sanitizers] Compiling fastpath with -fsanitize={sanitizer_type}...")
    cmd_so = [
        cc,
        *flags,
        "-I",
        inc_dir,
        "-shared",
        "-fPIC",
        "-o",
        out_so,
        src_c,
        "-lm",
        *extra_libs,
    ]
    res_so = subprocess.run(cmd_so, capture_output=True, text=True)
    if res_so.returncode != 0:
        if sys.platform == "win32" and (
            "cannot find -l" in res_so.stderr
            or "libasan" in res_so.stderr
            or "libubsan" in res_so.stderr
        ):
            print(
                f"[sanitizers] Notice: Windows MinGW GCC lacks {sanitizer_type} runtime library. Sanitizer verification is gated on Linux CI / WSL."
            )
            return True
        print(f"[sanitizers] Failed to build {out_so}:\n{res_so.stderr}")
        return False

    print(f"[sanitizers] Compiling mdrap_core with -fsanitize={sanitizer_type}...")
    cmd_core = [cc, *flags, "-I", inc_dir, "-o", out_core, core_c, "-lm", *extra_libs]
    res_core = subprocess.run(cmd_core, capture_output=True, text=True)
    if res_core.returncode != 0:
        if sys.platform == "win32" and (
            "cannot find -l" in res_core.stderr
            or "libasan" in res_core.stderr
            or "libubsan" in res_core.stderr
        ):
            print(
                f"[sanitizers] Notice: Windows MinGW GCC lacks {sanitizer_type} runtime library. Sanitizer verification is gated on Linux CI / WSL."
            )
            return True
        print(f"[sanitizers] Failed to build {out_core}:\n{res_core.stderr}")
        return False

    print(f"[sanitizers] Successfully built sanitizer targets for: {sanitizer_type}")
    return True


if __name__ == "__main__":
    san = sys.argv[1] if len(sys.argv) > 1 else "address"
    success = build_with_sanitizer(san)
    sys.exit(0 if success else 1)
