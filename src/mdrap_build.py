"""Setuptools commands for producing reproducible MDRAP package contents."""

from __future__ import annotations

import shutil
from setuptools.command.build_py import build_py


class CleanBuildPy(build_py):
    """Clean build directory to prevent stale build artifacts from leaking into wheels."""

    def run(self) -> None:
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()
