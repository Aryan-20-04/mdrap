"""MDRAP Standard Exceptions and Companion Package Boundary Enforcement."""

from __future__ import annotations

import os
import importlib.util

__stability__ = "stable"

EXIT_COMPANION_MISSING: int = 3


class CompanionPackageMissing(RuntimeError):
    """Raised when an optional domain feature requires an uninstalled companion package."""

    def __init__(self, package_name: str, install_hint: str) -> None:
        self.package_name = package_name
        self.install_hint = install_hint
        super().__init__(
            f"Companion package '{package_name}' is not installed.\n"
            f"Install via: {install_hint}"
        )


def require_companion(name: str, install_hint: str) -> None:
    """Validate that an optional companion module is available, or raise CompanionPackageMissing."""
    if os.environ.get("MDRAP_DISABLE_COMPANIONS") == "1":
        raise CompanionPackageMissing(name, install_hint)

    if importlib.util.find_spec(name) is None:
        raise CompanionPackageMissing(name, install_hint)
