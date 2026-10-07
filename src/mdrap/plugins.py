"""MDRAP Plugin and Extension Registry.

Discovers and loads third-party plugins declared in Python package entry points:
- mdrap.adapters: Feed adapters (FeedAdapter protocol)
- mdrap.quality_rules: Custom quality engine rules
- mdrap.storage_backends: Custom storage engines (AppendStorageSink / StorageBackend protocols)
- mdrap.auth_providers: Auth/entitlement providers (AuthProvider protocol)
- mdrap.output_sinks: Event delivery sinks (OutputSink protocol)
- mdrap.alert_sinks: Outbound alert sinks (AlertSink protocol)

Provides signature validation, version-handshake verification, and structured error reporting (Card #4).
"""

from __future__ import annotations

import inspect
import logging
import re
import sys
from typing import Any
from ._version import __version__

__stability__ = "stable"

logger = logging.getLogger("mdrap.plugins")

CURRENT_MDRAP_VERSION = __version__

PLUGIN_GROUPS = (
    "mdrap.adapters",
    "mdrap.quality_rules",
    "mdrap.storage_backends",
    "mdrap.auth_providers",
    "mdrap.output_sinks",
    "mdrap.alert_sinks",
)

__all__ = [
    "PLUGIN_GROUPS",
    "CURRENT_MDRAP_VERSION",
    "PluginValidationError",
    "PluginRegistry",
    "registry",
    "validate_plugin",
    "load_plugins",
    "discover_all_plugins",
    "load_adapters",
    "load_storage_backends",
    "load_auth_providers",
    "load_output_sinks",
    "load_alert_sinks",
    "load_quality_rules",
    "get_plugin",
    "get_adapter",
    "get_quality_rule",
    "get_storage_backend",
    "get_auth_provider",
    "get_output_sink",
    "get_alert_sink",
]


class PluginValidationError(Exception):
    """Raised when a plugin fails signature, contract, or version verification."""


def _parse_version(v_str: str) -> tuple[int, ...]:
    """Parse version string into integer tuple for comparison (e.g. '3.0.0' -> (3, 0, 0))."""
    digits = re.findall(r"\d+", str(v_str))
    return tuple(int(d) for d in digits) if digits else (0,)


def validate_plugin(group: str, plugin: Any) -> tuple[bool, str | None]:
    """Validate a plugin object or class against the contract and version requirements of its group.

    Returns:
        (True, None) if validation succeeds.
        (False, error_reason) if validation fails.
    """
    if group not in PLUGIN_GROUPS:
        return False, f"Unknown plugin group '{group}'. Must be one of {PLUGIN_GROUPS}"

    # 1. Version Handshake check
    min_version = getattr(plugin, "__min_mdrap_version__", None)
    if min_version is not None:
        if _parse_version(CURRENT_MDRAP_VERSION) < _parse_version(str(min_version)):
            return (
                False,
                f"Version mismatch: plugin requires MDRAP >= {min_version}, "
                f"current platform version is {CURRENT_MDRAP_VERSION}",
            )

    target_version = getattr(plugin, "__max_mdrap_version__", None)
    if target_version is not None:
        if _parse_version(CURRENT_MDRAP_VERSION) > _parse_version(str(target_version)):
            return (
                False,
                f"Version mismatch: plugin requires MDRAP <= {target_version}, "
                f"current platform version is {CURRENT_MDRAP_VERSION}",
            )

    # 2. Group-specific structural and signature checks
    cls_or_inst = plugin if not isinstance(plugin, type) else plugin

    if group == "mdrap.adapters":
        has_modern = hasattr(cls_or_inst, "receive") and (
            hasattr(cls_or_inst, "connect") or hasattr(cls_or_inst, "open")
        )
        has_stream = (
            hasattr(cls_or_inst, "open")
            and hasattr(cls_or_inst, "close")
            and (hasattr(cls_or_inst, "__iter__") or hasattr(cls_or_inst, "receive"))
        )
        if not (has_modern or has_stream):
            return (
                False,
                "Adapter contract violation: must implement either modern ('connect', 'receive') "
                "or stream ('open', 'close', '__iter__') interface.",
            )

        if hasattr(cls_or_inst, "normalize"):
            try:
                sig = inspect.signature(cls_or_inst.normalize)
                params = [
                    p
                    for p in sig.parameters.values()
                    if p.name not in ("self", "cls")
                    and p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                if len(params) < 1:
                    return (
                        False,
                        "Adapter normalize() signature error: must accept at least 1 positional parameter ('raw').",
                    )
            except (ValueError, TypeError):
                pass

    elif group == "mdrap.storage_backends":
        required_write_methods = (
            "write_canonical_batch",
            "write_quarantine_batch",
            "write_lineage_batch",
            "write_batches_atomic",
            "commit",
            "close",
        )
        missing = [m for m in required_write_methods if not hasattr(cls_or_inst, m)]
        if missing:
            return (
                False,
                f"Storage backend contract violation: missing required write methods: {missing}",
            )

        try:
            sig = inspect.signature(cls_or_inst.write_batches_atomic)
            param_names = [p for p in sig.parameters if p not in ("self", "cls")]
            has_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not has_var_kw and not any(
                p in param_names for p in ("canonical", "quarantine")
            ):
                return (
                    False,
                    "Storage write_batches_atomic signature error: missing 'canonical'/'quarantine' parameters.",
                )
        except (ValueError, TypeError):
            pass

    elif group == "mdrap.quality_rules":
        if not callable(plugin) and not hasattr(cls_or_inst, "evaluate"):
            return (
                False,
                "Quality rule contract violation: must be callable or provide an evaluate() method.",
            )

    elif group == "mdrap.auth_providers":
        required_auth_methods = ("get_entitlement", "authorize")
        missing = [m for m in required_auth_methods if not hasattr(cls_or_inst, m)]
        if missing:
            return (
                False,
                f"AuthProvider contract violation: missing required methods: {missing}",
            )

    elif group == "mdrap.output_sinks":
        required_sink_methods = ("broadcast_tick", "close")
        missing = [m for m in required_sink_methods if not hasattr(cls_or_inst, m)]
        if missing:
            return (
                False,
                f"OutputSink contract violation: missing required methods: {missing}",
            )

    elif group == "mdrap.alert_sinks":
        required_alert_methods = ("deliver", "close")
        missing = [m for m in required_alert_methods if not hasattr(cls_or_inst, m)]
        if missing:
            return (
                False,
                f"AlertSink contract violation: missing required methods: {missing}",
            )

    return True, None


class PluginRegistry:
    """Central registry and validator for all MDRAP plugin extensions."""

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = {g: {} for g in PLUGIN_GROUPS}
        self._errors: dict[str, dict[str, str]] = {g: {} for g in PLUGIN_GROUPS}

    def register(
        self,
        group: str,
        name: str,
        plugin: Any,
        validate: bool = True,
    ) -> None:
        """Register a plugin in a given group, optionally validating contract conformance."""
        if group not in PLUGIN_GROUPS:
            raise ValueError(
                f"Unknown plugin group '{group}'. Must be one of {PLUGIN_GROUPS}"
            )

        if validate:
            is_valid, err = validate_plugin(group, plugin)
            if not is_valid:
                error_msg = err or "Validation failed"
                self._errors[group][name] = error_msg
                raise PluginValidationError(
                    f"Plugin '{name}' in group '{group}' failed validation: {error_msg}"
                )

        self._plugins[group][name] = plugin
        self._errors[group].pop(name, None)

    def get(self, group: str, name: str) -> Any | None:
        """Retrieve a registered plugin by group and name."""
        return self._plugins.get(group, {}).get(name)

    def list(self, group: str) -> dict[str, Any]:
        """List all successfully registered plugins for a group."""
        return dict(self._plugins.get(group, {}))

    def get_errors(self, group: str) -> dict[str, str]:
        """Retrieve recorded validation failure reasons for a group."""
        return dict(self._errors.get(group, {}))

    def clear(self) -> None:
        """Clear all registered plugins and error records (primarily for testing)."""
        self._plugins = {g: {} for g in PLUGIN_GROUPS}
        self._errors = {g: {} for g in PLUGIN_GROUPS}

    def discover(
        self,
        group: str,
        validate: bool = True,
        raise_on_failure: bool = False,
    ) -> dict[str, Any]:
        """Discover and load plugins declared via package entry points."""
        if group not in PLUGIN_GROUPS:
            logger.warning("Attempted discovery for unknown plugin group '%s'", group)
            return {}

        try:
            if sys.version_info >= (3, 10):
                from importlib.metadata import entry_points

                eps = entry_points(group=group)
            else:
                import importlib_metadata  # type: ignore[import-untyped]

                eps = importlib_metadata.entry_points().get(group, [])

            for ep in eps:
                ep_name = getattr(ep, "name", "unknown")
                try:
                    obj = ep.load()
                    if validate:
                        is_valid, err = validate_plugin(group, obj)
                        if not is_valid:
                            reason = err or "Validation check failed"
                            self._errors[group][ep_name] = reason
                            logger.warning(
                                "Plugin '%s' in '%s' failed validation: %s",
                                ep_name,
                                group,
                                reason,
                            )
                            if raise_on_failure:
                                raise PluginValidationError(
                                    f"Plugin '{ep_name}' failed validation: {reason}"
                                )
                            continue

                    self._plugins[group][ep_name] = obj
                    self._errors[group].pop(ep_name, None)
                except Exception as exc:
                    self._errors[group][ep_name] = str(exc)
                    logger.warning(
                        "Failed to load or validate plugin '%s' from group '%s': %s",
                        ep_name,
                        group,
                        exc,
                    )
                    if raise_on_failure:
                        raise
        except Exception as exc:
            logger.debug("Entry points lookup failed for group '%s': %s", group, exc)
            if raise_on_failure:
                raise

        return dict(self._plugins[group])


# Global institutional plugin registry instance
registry: PluginRegistry = PluginRegistry()


def load_plugins(
    group: str,
    validate: bool = True,
    raise_on_failure: bool = False,
) -> dict[str, Any]:
    """Load installed plugins for a specific entry-point group using the central registry."""
    return registry.discover(
        group, validate=validate, raise_on_failure=raise_on_failure
    )


def discover_all_plugins(validate: bool = True) -> dict[str, dict[str, Any]]:
    """Discover and return all installed plugins organized by group."""
    return {group: load_plugins(group, validate=validate) for group in PLUGIN_GROUPS}


def load_adapters(validate: bool = True) -> dict[str, Any]:
    """Discover third-party feed adapters."""
    return load_plugins("mdrap.adapters", validate=validate)


def load_storage_backends(validate: bool = True) -> dict[str, Any]:
    """Discover third-party storage backends."""
    return load_plugins("mdrap.storage_backends", validate=validate)


def load_auth_providers(validate: bool = True) -> dict[str, Any]:
    """Discover third-party authentication providers."""
    return load_plugins("mdrap.auth_providers", validate=validate)


def load_output_sinks(validate: bool = True) -> dict[str, Any]:
    """Discover third-party output sinks."""
    return load_plugins("mdrap.output_sinks", validate=validate)


def load_alert_sinks(validate: bool = True) -> dict[str, Any]:
    """Discover third-party alert sinks."""
    return load_plugins("mdrap.alert_sinks", validate=validate)


def load_quality_rules(validate: bool = True) -> dict[str, Any]:
    """Discover third-party quality rules."""
    return load_plugins("mdrap.quality_rules", validate=validate)


def get_plugin(group: str, name: str, default: Any = None) -> Any:
    """Retrieve a registered plugin by group and name, attempting discovery if not yet cached."""
    p = registry.get(group, name)
    if p is not None:
        return p
    plugins = registry.discover(group, validate=False)
    return plugins.get(name, default)


def get_adapter(name: str, default: Any = None) -> Any:
    """Resolve feed adapter plugin by name."""
    return get_plugin("mdrap.adapters", name, default=default)


def get_quality_rule(name: str, default: Any = None) -> Any:
    """Resolve custom quality rule plugin by name."""
    return get_plugin("mdrap.quality_rules", name, default=default)


def get_storage_backend(name: str, default: Any = None) -> Any:
    """Resolve storage backend plugin by name."""
    return get_plugin("mdrap.storage_backends", name, default=default)


def get_auth_provider(name: str, default: Any = None) -> Any:
    """Resolve authentication provider plugin by name."""
    return get_plugin("mdrap.auth_providers", name, default=default)


def get_output_sink(name: str, default: Any = None) -> Any:
    """Resolve output sink plugin by name."""
    return get_plugin("mdrap.output_sinks", name, default=default)


def get_alert_sink(name: str, default: Any = None) -> Any:
    """Resolve alert sink plugin by name."""
    return get_plugin("mdrap.alert_sinks", name, default=default)
