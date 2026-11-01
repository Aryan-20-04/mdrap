"""Setuptools commands for producing reproducible MDRAP package contents."""

from __future__ import annotations

import shutil
from setuptools.command.build_py import build_py

_CORE_MODULES = {
    "__init__",
    "_version",
    "analytics",
    "archive",
    "audit_format",
    "bbo",
    "client",
    "config",
    "fastpath",
    "gateway",
    "metrics",
    "models",
    "pipeline",
    "plugins",
    "protocol",
    "protocols",
    "quality",
    "quarantine",
    "reconciliation",
    "rules",
    "sbe",
    "security",
    "shm",
    "storage",
    "symbology",
    "watchdog",
}


class CleanBuildPy(build_py):
    """Clean build directory to prevent stale build artifacts from leaking into wheels."""

    def run(self) -> None:
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()
