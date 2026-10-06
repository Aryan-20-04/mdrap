"""MDRAP Plugin and Extension Registry.

Discovers and loads third-party plugins declared in Python package entry points:
- mdrap.adapters: Feed adapters
- mdrap.quality_rules: Custom quality engine rules
- mdrap.storage_backends: Custom storage engines
- mdrap.auth_providers: Auth/entitlement providers
- mdrap.output_sinks: Event delivery sinks
- mdrap.alert_sinks: Outbound alert sinks
"""

from __future__ import annotations

import logging
import sys
from typing import Any

__stability__ = "stable"

logger = logging.getLogger("mdrap.plugins")

PLUGIN_GROUPS = (
    "mdrap.adapters",
    "mdrap.quality_rules",
    "mdrap.storage_backends",
    "mdrap.auth_providers",
    "mdrap.output_sinks",
    "mdrap.alert_sinks",
)


def load_plugins(group: str) -> dict[str, Any]:
    """Load installed plugins for a specific entry-point group."""
    results: dict[str, Any] = {}
    try:
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points

            eps = entry_points(group=group)
        else:
            import importlib_metadata  # type: ignore[import-untyped]

            eps = importlib_metadata.entry_points().get(group, [])

        for ep in eps:
            try:
                results[ep.name] = ep.load()
            except Exception as exc:
                logger.warning(
                    "Failed to load plugin '%s' from group '%s': %s",
                    getattr(ep, "name", "unknown"),
                    group,
                    exc,
                )
    except Exception as exc:
        logger.debug("Entry points lookup failed for group '%s': %s", group, exc)
    return results


def discover_all_plugins() -> dict[str, dict[str, Any]]:
    """Discover and return all installed plugins organized by group."""
    return {group: load_plugins(group) for group in PLUGIN_GROUPS}


def load_adapters() -> dict[str, Any]:
    """Discover third-party feed adapters."""
    return load_plugins("mdrap.adapters")


def load_storage_backends() -> dict[str, Any]:
    """Discover third-party storage backends."""
    return load_plugins("mdrap.storage_backends")


def load_auth_providers() -> dict[str, Any]:
    """Discover third-party authentication providers."""
    return load_plugins("mdrap.auth_providers")


def load_output_sinks() -> dict[str, Any]:
    """Discover third-party output sinks."""
    return load_plugins("mdrap.output_sinks")


def load_alert_sinks() -> dict[str, Any]:
    """Discover third-party alert sinks."""
    return load_plugins("mdrap.alert_sinks")


def load_quality_rules() -> dict[str, Any]:
    """Discover third-party quality rules."""
    return load_plugins("mdrap.quality_rules")
