"""Setuptools commands for producing reproducible MDRAP package contents."""

from __future__ import annotations

import shutil
from setuptools.command.build_py import build_py

class CleanBuildPy(build_py):
    """Clean build directory to prevent stale build artifacts from leaking into wheels."""

    def run(self) -> None:
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()
        # Post-process build_lib to ensure zero compiled binaries leak into pure-Python wheels (PEP 427)
        import os
        for root, _, files in os.walk(self.build_lib):
            for file in files:
                if file.endswith((".dll", ".so", ".pyd", ".dylib")):
                    try:
                        os.remove(os.path.join(root, file))
                    except OSError:
                        pass
