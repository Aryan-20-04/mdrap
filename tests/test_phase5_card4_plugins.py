import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.plugins import (
    PLUGIN_GROUPS,
    discover_all_plugins,
    load_plugins,
    load_adapters,
)


def test_discover_all_plugin_groups():
    discovered = discover_all_plugins()
    for grp in PLUGIN_GROUPS:
        assert grp in discovered
        assert isinstance(discovered[grp], dict)


def test_adapter_plugin_loading():
    adapters = load_adapters()
    # If package is installed in editable mode or entry point resolved, template adapter is found
    # Otherwise discover_all_plugins still behaves safely without errors
    assert isinstance(adapters, dict)
    if "template" in adapters:
        adapter_cls = adapters["template"]
        instance = adapter_cls("TEST_VENUE")
        assert hasattr(instance, "open")
        assert hasattr(instance, "close")
