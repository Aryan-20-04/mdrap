import glob
import os
import pytest

VALID_STABILITIES = {"stable", "beta", "experimental"}


def test_all_src_modules_declare_valid_stability_contract():
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
    py_files = sorted(glob.glob(os.path.join(src_dir, "*.py")))

    missing = []
    invalid = []

    for path in py_files:
        base = os.path.basename(path)
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Look for __stability__ = "..."
        found = False
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("__stability__") and "=" in line:
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val not in VALID_STABILITIES:
                    invalid.append((base, val))
                found = True
                break

        if not found:
            missing.append(base)

    assert not missing, f"Modules missing __stability__ contract: {missing}"
    assert not invalid, f"Modules with invalid __stability__ values: {invalid}"


def test_deprecated_decorator_emits_warning():
    from models import deprecated

    @deprecated(
        since="2.5.0", removal="3.1.0", replacement="new_func", message="legacy API"
    )
    def legacy_func(x):
        return x * 2

    assert getattr(legacy_func, "__deprecated__", False) is True
    with pytest.deprecated_call():
        res = legacy_func(5)
    assert res == 10


def test_public_api_surface_snapshot_preserved():
    """Verify that public API surface contracts across stable core modules remain intact (Card #10)."""
    import mdrap
    import models
    import protocols
    import pipeline
    import gateway
    import storage
    import plugins

    expected_surfaces = {
        mdrap: [
            "Client",
            "MDRAPClient",
            "MarketEvent",
            "CanonicalEvent",
            "EventType",
            "QualityStatus",
            "Reason",
            "deprecated",
            "__version__",
        ],
        models: [
            "CanonicalEvent",
            "RawEvent",
            "EventType",
            "QualityStatus",
            "Reason",
            "deprecated",
        ],
        protocols: [
            "AppendStorageSink",
            "QueryStorageStore",
            "StorageBackend",
            "AuthProvider",
            "QualityEvaluator",
            "OutputSink",
            "AlertSink",
            "DeliveryResult",
        ],
        pipeline: ["Pipeline"],
        gateway: ["normalize", "ingest", "SchemaError"],
        storage: ["Store"],
        plugins: [
            "PluginRegistry",
            "PluginValidationError",
            "registry",
            "validate_plugin",
            "load_plugins",
            "discover_all_plugins",
        ],
    }

    missing_symbols = {}
    for mod, symbols in expected_surfaces.items():
        mod_name = mod.__name__
        missing = [sym for sym in symbols if not hasattr(mod, sym)]
        if missing:
            missing_symbols[mod_name] = missing

    assert not missing_symbols, (
        f"Breaking API changes detected: missing symbols: {missing_symbols}"
    )
