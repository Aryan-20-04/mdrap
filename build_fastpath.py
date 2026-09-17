"""
Build script for MDRAP Native C Hot-Path Accelerator.
Compiles src/fastpath.c to produce:
  - _fastpath_native.dll (Windows)
  - _fastpath_native.so (Linux / Unix)
  - _fastpath_native.dylib / _fastpath_native.so (macOS)

Supports GCC, Clang, and MSVC (cl.exe).
Can be executed standalone (`python build_fastpath.py`) or called programmatically
by setup.py and fastpath.py JIT loader.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional


def get_lib_name(extension: Optional[str] = None) -> str:
    """Return platform-standard shared library name."""
    if extension:
        ext = extension.lstrip(".")
        return f"_fastpath_native.{ext}"
    if sys.platform == "win32":
        return "_fastpath_native.dll"
    elif sys.platform == "darwin":
        return "_fastpath_native.dylib"
    else:
        return "_fastpath_native.so"


def find_source_file(custom_dir: Optional[str] = None) -> Optional[str]:
    """Locate fastpath.c in the project hierarchy."""
    candidates = []
    if custom_dir:
        candidates.append(os.path.join(custom_dir, "fastpath.c"))
        candidates.append(os.path.join(custom_dir, "src", "fastpath.c"))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        os.path.join(base_dir, "src", "fastpath.c"),
        os.path.join(base_dir, "fastpath.c"),
        os.path.abspath("src/fastpath.c"),
        os.path.abspath("fastpath.c"),
    ])

    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def _find_compiler() -> Optional[str]:
    """Detect available C compiler on the system PATH."""
    for compiler in ("gcc", "clang", "cl"):
        if shutil.which(compiler):
            return compiler
    return None


def build(target_dir: Optional[str] = None, quiet: bool = False) -> bool:
    """
    Compile fastpath.c into target_dir.
    Returns True on success, False otherwise.
    """
    c_source = find_source_file(target_dir)
    if not c_source or not os.path.isfile(c_source):
        if not quiet:
            print(f"[build] Error: Could not locate fastpath.c")
        return False

    if target_dir is None:
        target_dir = os.path.dirname(c_source)
    os.makedirs(target_dir, exist_ok=True)

    lib_name = get_lib_name()
    out_lib = os.path.normpath(os.path.join(target_dir, lib_name))

    compiler = _find_compiler()
    if not compiler:
        src_dir = os.path.dirname(c_source)
        legacy_name = (
            "fastpath.dll"
            if sys.platform == "win32"
            else ("fastpath.dylib" if sys.platform == "darwin" else "fastpath.so")
        )
        legacy_file = os.path.join(src_dir, legacy_name)
        if os.path.isfile(legacy_file) and not os.path.isfile(out_lib):
            try:
                shutil.copy2(legacy_file, out_lib)
                if not quiet:
                    print(f"[build] Copied legacy {legacy_file} -> {out_lib}")
                return True
            except OSError:
                pass
        if not quiet:
            print("[build] Notice: No C compiler (gcc, clang, cl) found on PATH.")
        return False

    # Include directories: source directory, target directory, and project src directory
    inc_dirs = [os.path.dirname(os.path.abspath(c_source))]
    if target_dir:
        inc_dirs.append(os.path.abspath(target_dir))
    base_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    if os.path.isdir(base_src):
        inc_dirs.append(base_src)

    # De-duplicate while preserving order
    unique_incs = list(dict.fromkeys(inc_dirs))

    if compiler in ("gcc", "clang"):
        cmd = [
            compiler,
            "-O3",
            "-shared",
            "-fPIC",
        ]
        for inc in unique_incs:
            cmd.extend(["-I", inc])
        if sys.platform == "win32":
            cmd.extend(["-Wl,--enable-stdcall-fixup"])
        elif sys.platform == "darwin":
            cmd.extend(["-dynamiclib"])
        cmd.extend(["-o", out_lib, c_source])
    elif compiler == "cl":
        cmd = [
            "cl.exe",
            "/O2",
            "/LD",
        ]
        for inc in unique_incs:
            cmd.append(f"/I{inc}")
        cmd.extend([
            c_source,
            f"/Fe:{out_lib}",
        ])
    else:
        return False

    if not quiet:
        print(f"[build] Compiling {os.path.basename(c_source)} -> {out_lib} using {compiler}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30.0,
            shell=False,
        )
    except Exception as exc:
        if not quiet:
            print(f"[build] Compilation error: {exc}")
        return False

    if result.returncode == 0 and os.path.isfile(out_lib):
        if sys.platform == "darwin" and lib_name == "_fastpath_native.dylib":
            so_link = os.path.normpath(os.path.join(target_dir, "_fastpath_native.so"))
            try:
                shutil.copy2(out_lib, so_link)
            except OSError:
                pass

        if not quiet:
            size = os.path.getsize(out_lib)
            print(f"[build] SUCCESS! Compiled {out_lib} ({size:,} bytes)")
        return True
    else:
        if not quiet:
            err_msg = result.stderr.strip() or result.stdout.strip()
            print(f"[build] FAILED with returncode {result.returncode}:\n{err_msg}")
        return False


if __name__ == "__main__":
    out_target = sys.argv[1] if len(sys.argv) > 1 else None
    success = build(target_dir=out_target, quiet=False)
    sys.exit(0 if success else 1)
