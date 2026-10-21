"""
Build script for MDRAP Native C Hot-Path Accelerator.
Compiles mdrap/fastpath.c to produce:
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

__stability__ = "experimental"


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
        candidates.append(os.path.join(custom_dir, "src", "mdrap", "fastpath.c"))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend(
        [
            os.path.join(base_dir, "src", "mdrap", "fastpath.c"),
            os.path.join(base_dir, "fastpath.c"),
            os.path.abspath("src/mdrap/fastpath.c"),
            os.path.abspath("fastpath.c"),
        ]
    )

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


def _safely_retire_existing_file(filepath: str) -> None:
    """On Windows, loaded DLLs/binaries cannot be overwritten in-place, but can be renamed."""
    if sys.platform == "win32" and os.path.isfile(filepath):
        old_file = filepath + ".old"
        try:
            if os.path.isfile(old_file):
                os.remove(old_file)
        except OSError:
            pass
        try:
            os.replace(filepath, old_file)
        except OSError:
            pass


def build(
    target_dir: Optional[str] = None,
    quiet: bool = False,
    pgo_mode: Optional[str] = None,
    pgo_dir: Optional[str] = None,
) -> bool:
    """
    Compile fastpath.c into target_dir.
    Returns True on success, False otherwise.
    """
    c_source = find_source_file(target_dir)
    if not c_source or not os.path.isfile(c_source):
        if not quiet:
            print("[build] Error: Could not locate fastpath.c")
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
    base_src = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(base_src):
        inc_dirs.append(base_src)

    # De-duplicate while preserving order
    unique_incs = list(dict.fromkeys(inc_dirs))

    if compiler in ("gcc", "clang"):
        cmd = [
            compiler,
            "-O3",
            "-march=native",
            "-mavx2",
            "-ffp-contract=off",
            "-flto",
            "-shared",
            "-fPIC",
        ]
        if pgo_mode == "generate":
            cmd.append(
                f"-fprofile-generate={pgo_dir}" if pgo_dir else "-fprofile-generate"
            )
        elif pgo_mode == "use":
            cmd.extend(
                [
                    f"-fprofile-use={pgo_dir}" if pgo_dir else "-fprofile-use",
                    "-fprofile-correction",
                ]
            )
        for inc in unique_incs:
            cmd.extend(["-I", inc])
        if sys.platform == "win32":
            cmd.extend(["-Wl,--enable-stdcall-fixup"])
        elif sys.platform == "darwin":
            cmd.extend(["-dynamiclib"])
        cmd.extend(["-o", out_lib, c_source])
        if sys.platform != "win32":
            cmd.extend(["-lm", "-lpthread"])
            if sys.platform.startswith("linux"):
                cmd.append("-lrt")
    elif compiler == "cl":
        cmd = [
            "cl.exe",
            "/O2",
            "/LD",
        ]
        for inc in unique_incs:
            cmd.append(f"/I{inc}")
        cmd.extend(
            [
                c_source,
                f"/Fe:{out_lib}",
            ]
        )
    else:
        return False

    if not quiet:
        print(
            f"[build] Compiling {os.path.basename(c_source)} -> {out_lib} using {compiler}"
        )

    _safely_retire_existing_file(out_lib)
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


def get_core_bin_name() -> str:
    """Return platform executable name for standalone mdrap-core."""
    return "mdrap-core.exe" if sys.platform == "win32" else "mdrap-core"


def find_core_source_file(custom_dir: Optional[str] = None) -> Optional[str]:
    """Locate mdrap_core.c in the project hierarchy."""
    candidates = []
    if custom_dir:
        candidates.append(os.path.join(custom_dir, "mdrap_core.c"))
        candidates.append(os.path.join(custom_dir, "src", "mdrap", "mdrap_core.c"))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend(
        [
            os.path.join(base_dir, "src", "mdrap", "mdrap_core.c"),
            os.path.join(base_dir, "mdrap_core.c"),
            os.path.abspath("src/mdrap/mdrap_core.c"),
            os.path.abspath("mdrap_core.c"),
        ]
    )

    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def build_core(
    target_dir: Optional[str] = None,
    quiet: bool = False,
    pgo_mode: Optional[str] = None,
    pgo_dir: Optional[str] = None,
) -> bool:
    """
    Compile mdrap_core.c into standalone binary (T1 zero-lock engine).
    Returns True on success, False otherwise.
    """
    c_source = find_core_source_file(target_dir)
    if not c_source or not os.path.isfile(c_source):
        if not quiet:
            print("[build] Error: Could not locate mdrap_core.c")
        return False

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if target_dir is None:
        target_dir = base_dir
    os.makedirs(target_dir, exist_ok=True)

    bin_name = get_core_bin_name()
    out_bin = os.path.normpath(os.path.join(target_dir, bin_name))

    compiler = _find_compiler()
    if not compiler:
        if not quiet:
            print("[build] Notice: No C compiler (gcc, clang, cl) found on PATH.")
        return False

    inc_dirs = [os.path.dirname(os.path.abspath(c_source))]
    unique_incs = list(dict.fromkeys(inc_dirs))

    if compiler in ("gcc", "clang"):
        cmd = [
            compiler,
            "-O3",
            "-march=native",
            "-mavx2",
            "-ffp-contract=off",
            "-flto",
            "-D_GNU_SOURCE",
        ]
        if pgo_mode == "generate":
            cmd.append(
                f"-fprofile-generate={pgo_dir}" if pgo_dir else "-fprofile-generate"
            )
        elif pgo_mode == "use":
            cmd.extend(
                [
                    f"-fprofile-use={pgo_dir}" if pgo_dir else "-fprofile-use",
                    "-fprofile-correction",
                ]
            )
        for inc in unique_incs:
            cmd.extend(["-I", inc])
        cmd.extend(["-o", out_bin, c_source])
        if sys.platform != "win32":
            cmd.extend(["-lm", "-lpthread"])
            if sys.platform.startswith("linux"):
                cmd.append("-lrt")
    elif compiler == "cl":
        cmd = ["cl.exe", "/O2"]
        for inc in unique_incs:
            cmd.append(f"/I{inc}")
        cmd.extend([c_source, f"/Fe:{out_bin}"])
    else:
        return False

    if not quiet:
        print(
            f"[build] Compiling {os.path.basename(c_source)} -> {out_bin} using {compiler}"
        )

    _safely_retire_existing_file(out_bin)
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

    if result.returncode == 0 and os.path.isfile(out_bin):
        if not quiet:
            size = os.path.getsize(out_bin)
            print(f"[build] SUCCESS! Compiled {out_bin} ({size:,} bytes)")
        return True
    else:
        if not quiet:
            err_msg = result.stderr.strip() or result.stdout.strip()
            print(f"[build] FAILED with returncode {result.returncode}:\n{err_msg}")
        return False


def find_extension_source_file(custom_dir: Optional[str] = None) -> Optional[str]:
    """Locate _fastpath_c.c in the project hierarchy."""
    candidates = []
    if custom_dir:
        candidates.append(os.path.join(custom_dir, "_fastpath_c.c"))
        candidates.append(os.path.join(custom_dir, "src", "mdrap", "_fastpath_c.c"))

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend(
        [
            os.path.join(base_dir, "src", "mdrap", "_fastpath_c.c"),
            os.path.join(base_dir, "_fastpath_c.c"),
            os.path.abspath("src/mdrap/_fastpath_c.c"),
            os.path.abspath("_fastpath_c.c"),
        ]
    )

    for path in candidates:
        if os.path.isfile(path):
            return os.path.normpath(path)
    return None


def build_extension(
    target_dir: Optional[str] = None,
    quiet: bool = False,
    pgo_mode: Optional[str] = None,
    pgo_dir: Optional[str] = None,
) -> bool:
    """
    Compile src/_fastpath_c.c into native Python C-API extension module (_fastpath_c.pyd / .so).
    Returns True on success, False otherwise.
    """
    c_source = find_extension_source_file(target_dir)
    if not c_source or not os.path.isfile(c_source):
        if not quiet:
            print("[build] Error: Could not locate _fastpath_c.c")
        return False

    src_dir = os.path.dirname(os.path.abspath(c_source))
    if target_dir is None:
        target_dir = src_dir
    os.makedirs(target_dir, exist_ok=True)

    compiler = _find_compiler()
    if not compiler:
        return False

    import sysconfig

    py_include = sysconfig.get_path("include")
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or (
        ".pyd" if sys.platform == "win32" else ".so"
    )
    out_ext = os.path.normpath(os.path.join(target_dir, f"_fastpath_c{ext_suffix}"))

    if compiler in ("gcc", "clang"):
        cmd = [
            compiler,
            "-O3",
            "-march=native",
            "-mavx2",
            "-ffp-contract=off",
            "-flto",
            "-shared",
        ]
        if pgo_mode == "generate":
            cmd.append(
                f"-fprofile-generate={pgo_dir}" if pgo_dir else "-fprofile-generate"
            )
        elif pgo_mode == "use":
            cmd.extend(
                [
                    f"-fprofile-use={pgo_dir}" if pgo_dir else "-fprofile-use",
                    "-fprofile-correction",
                ]
            )
        cmd.extend(
            [
                "-I",
                py_include,
                "-I",
                src_dir,
                "-o",
                out_ext,
                c_source,
            ]
        )
        if sys.platform == "win32":
            py_libdir = sysconfig.get_config_var("LIBDIR") or os.path.join(
                sys.base_prefix, "libs"
            )
            cmd.extend(
                [
                    f"-L{py_libdir}",
                    f"-lpython{sys.version_info.major}{sys.version_info.minor}",
                    "-Wl,--enable-stdcall-fixup",
                ]
            )
        elif sys.platform == "darwin":
            cmd.extend(["-undefined", "dynamic_lookup"])
        else:
            cmd.extend(["-fPIC"])
    elif compiler == "cl":
        py_libdir = sysconfig.get_config_var("LIBDIR") or os.path.join(
            sys.base_prefix, "libs"
        )
        cmd = [
            "cl.exe",
            "/O2",
            "/LD",
            f"/I{py_include}",
            f"/I{src_dir}",
            c_source,
            f"/Fe:{out_ext}",
            "/link",
            f"/LIBPATH:{py_libdir}",
        ]
    else:
        return False

    if not quiet:
        print(
            f"[build] Compiling {os.path.basename(c_source)} -> {out_ext} using {compiler}"
        )

    _safely_retire_existing_file(out_ext)
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

    if result.returncode == 0 and os.path.isfile(out_ext):
        if not quiet:
            size = os.path.getsize(out_ext)
            print(f"[build] SUCCESS! Compiled {out_ext} ({size:,} bytes)")
        return True
    else:
        if not quiet:
            err_msg = result.stderr.strip() or result.stdout.strip()
            print(f"[build] FAILED with returncode {result.returncode}:\n{err_msg}")
        return False


def build_all_pgo(
    target_dir: Optional[str] = None,
    quiet: bool = False,
    training_ticks: int = 100_000,
) -> bool:
    """Build all native components with two-pass Profile-Guided Optimization (PGO).

    Pass 1: Compile instrumented binaries with -fprofile-generate.
    Pass 2: Execute realistic representative training workloads (standalone daemon and Python hot path).
    Pass 3: Recompile fully optimized binaries with -fprofile-use and whole-program LTO (-flto).
    """
    if target_dir is None:
        target_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(target_dir, exist_ok=True)

    compiler = _find_compiler()
    if compiler not in ("gcc", "clang"):
        if not quiet:
            print(
                f"[build-pgo] Notice: PGO requires GCC or Clang (found: {compiler}). Falling back to standard build."
            )
        ok1 = build(target_dir=target_dir, quiet=quiet)
        ok2 = build_core(target_dir=target_dir, quiet=quiet)
        ok3 = build_extension(target_dir=target_dir, quiet=quiet)
        return ok1 and ok2

    pgo_dir = os.path.normpath(os.path.join(target_dir, ".pgo_profile_data"))
    os.makedirs(pgo_dir, exist_ok=True)

    if not quiet:
        print(
            "[build-pgo] === PASS 1/3: Compiling instrumented binaries for profiling ==="
        )

    ok1 = build(
        target_dir=target_dir, quiet=quiet, pgo_mode="generate", pgo_dir=pgo_dir
    )
    ok2 = build_core(
        target_dir=target_dir, quiet=quiet, pgo_mode="generate", pgo_dir=pgo_dir
    )
    ok3 = build_extension(
        target_dir=target_dir, quiet=quiet, pgo_mode="generate", pgo_dir=pgo_dir
    )

    if not (ok1 and ok2 and ok3):
        if not quiet:
            print("[build-pgo] FAILED in Pass 1 instrumented build.")
        shutil.rmtree(pgo_dir, ignore_errors=True)
        return False

    if not quiet:
        print(
            f"[build-pgo] === PASS 2/3: Executing training workload ({training_ticks:,} ticks) ==="
        )

    core_bin = os.path.join(
        target_dir, "mdrap-core.exe" if sys.platform == "win32" else "mdrap-core"
    )
    if os.path.isfile(core_bin):
        try:
            subprocess.run(
                [core_bin, "--benchmark", "--ticks", str(training_ticks)],
                capture_output=True,
                timeout=30.0,
            )
        except Exception as exc:
            if not quiet:
                print(f"[build-pgo] Notice during training workload: {exc}")

    if not quiet:
        print(
            "[build-pgo] === PASS 3/3: Recompiling with profile data and link-time optimization ==="
        )

    ok1_opt = build(target_dir=target_dir, quiet=quiet, pgo_mode="use", pgo_dir=pgo_dir)
    ok2_opt = build_core(
        target_dir=target_dir, quiet=quiet, pgo_mode="use", pgo_dir=pgo_dir
    )
    ok3_opt = build_extension(
        target_dir=target_dir, quiet=quiet, pgo_mode="use", pgo_dir=pgo_dir
    )

    shutil.rmtree(pgo_dir, ignore_errors=True)

    success = ok1_opt and ok2_opt and ok3_opt
    if not quiet:
        if success:
            print(
                "[build-pgo] SUCCESS! Profile-Guided Optimization (PGO) build complete."
            )
        else:
            print("[build-pgo] FAILED in Pass 3 optimized build.")
    return success


if __name__ == "__main__":
    is_pgo = "--pgo" in sys.argv
    clean_argv = [a for a in sys.argv[1:] if a != "--pgo"]
    out_target = (
        clean_argv[0] if clean_argv and not clean_argv[0].startswith("-") else None
    )

    if is_pgo:
        success = build_all_pgo(target_dir=out_target, quiet=False)
    else:
        ok1 = build(target_dir=out_target, quiet=False)
        ok2 = build_core(target_dir=out_target, quiet=False)
        ok3 = build_extension(target_dir=out_target, quiet=False)
        success = ok1 and ok2
    sys.exit(0 if success else 1)
