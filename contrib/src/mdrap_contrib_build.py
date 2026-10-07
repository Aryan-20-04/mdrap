"""Setuptools command for the MDRAP peripheral distribution."""

from __future__ import annotations

import shutil

from setuptools.command.build_py import build_py

_CORE_MODULES = {
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


class ContribBuildPy(build_py):
    """Build only add-on modules into the shared mdrap namespace."""

    def run(self) -> None:
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()

    def find_package_modules(self, package: str, package_dir: str):
        modules = super().find_package_modules(package, package_dir)
        if package == "mdrap":
            return [item for item in modules if item[1] not in _CORE_MODULES]
        return modules
