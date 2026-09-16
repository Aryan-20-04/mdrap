import os
import glob
import sys
from setuptools import setup, find_packages
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop


# Ensure current directory is on sys.path for build_fastpath import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class BuildPyWithFastpath(build_py):
    """Automatically compile native C hot-path during package build and pip install."""

    def run(self):
        super().run()
        try:
            import build_fastpath
            base_dir = os.path.dirname(os.path.abspath(__file__))
            # Compile into wheel/install target directory
            build_fastpath.build(target_dir=self.build_lib, quiet=False)
            # Copy fastpath.c and build_fastpath.py into wheel target so JIT works anywhere
            src_c = os.path.join(base_dir, "src", "fastpath.c")
            if os.path.isfile(src_c):
                import shutil
                shutil.copy2(src_c, os.path.join(self.build_lib, "fastpath.c"))
            bfp = os.path.join(base_dir, "build_fastpath.py")
            if os.path.isfile(bfp):
                import shutil
                shutil.copy2(bfp, os.path.join(self.build_lib, "build_fastpath.py"))
            # Also copy precompiled shared libraries from src/ if they exist
            for lib in (
                "_fastpath_native.dll",
                "_fastpath_native.so",
                "_fastpath_native.dylib",
                "_fastpath_native.pyd",
                "fastpath.dll",
                "fastpath.dylib",
            ):
                src_lib = os.path.join(base_dir, "src", lib)
                dest_lib = os.path.join(self.build_lib, lib)
                if os.path.isfile(src_lib) and not os.path.isfile(dest_lib):
                    import shutil
                    shutil.copy2(src_lib, dest_lib)
            # Also compile into src/ for in-tree execution
            src_dir = os.path.join(base_dir, "src")
            build_fastpath.build(target_dir=src_dir, quiet=True)
        except Exception as exc:
            print(f"[setup] Notice: Native C fastpath compilation skipped: {exc}")


class DevelopWithFastpath(develop):
    """Automatically compile native C hot-path during pip install -e ."""

    def run(self):
        try:
            import build_fastpath
            src_dir = os.path.join(os.path.dirname(__file__), "src")
            build_fastpath.build(target_dir=src_dir, quiet=False)
        except Exception as exc:
            print(f"[setup] Notice: Native C fastpath compilation skipped: {exc}")
        super().run()


# Find all root-level python files in src/
src_files = glob.glob("src/*.py")
py_modules = list(set(["cli"] + [os.path.basename(f)[:-3] for f in src_files if not f.endswith("__init__.py")]))

setup(
    name="mdrap",
    version="1.2.1",
    description="Market Data Reliability & Acceleration Platform",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    py_modules=py_modules,
    package_data={
        "": [
            "fastpath.c",
            "_fastpath_native.dll",
            "_fastpath_native.so",
            "_fastpath_native.dylib",
            "_fastpath_native.pyd",
            "fastpath.dll",
            "fastpath.dylib",
        ],
    },
    include_package_data=True,
    cmdclass={
        "build_py": BuildPyWithFastpath,
        "develop": DevelopWithFastpath,
    },
    entry_points={
        "console_scripts": [
            "mdrap=cli:main",
        ],
    },
)
