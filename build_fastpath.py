"""
Build script for MDRAP Native C Hot-Path Accelerator.
Invokes gcc to produce src/fastpath.dll (Windows) or src/fastpath.so (Linux/macOS).
"""
import os
import subprocess
import sys

def build():
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    c_source = os.path.join(src_dir, "fastpath.c")
    out_lib = os.path.join(src_dir, "fastpath.dll" if sys.platform == "win32" else "fastpath.so")

    cmd = [
        "gcc",
        "-O3",
        "-shared",
        "-fPIC",
        "-o", out_lib,
        c_source,
    ]

    print(f"[build] Compiling {c_source} -> {out_lib}")
    print(f"[build] Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[build] SUCCESS! Compiled {out_lib} ({os.path.getsize(out_lib):,} bytes)")
        return True
    else:
        print(f"[build] FAILED with code {result.returncode}:\n{result.stderr}")
        return False

if __name__ == "__main__":
    success = build()
    sys.exit(0 if success else 1)
