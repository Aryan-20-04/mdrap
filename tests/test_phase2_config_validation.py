"""
Phase 2 Configuration Validation Tests (Workstream E).

Verifies:
1. Valid configuration dictionaries pass validation cleanly.
2. Unknown top-level sections raise ConfigValidationError with list of allowed sections.
3. Unknown or misspelled keys within sections raise ConfigValidationError.
4. Out-of-bounds numeric parameters fail closed.
5. Strict precedence hierarchy: CLI > Environment > File > Default.
"""

import os
import pytest

from mdrap.config_loader import (
    ConfigValidationError,
    load_config,
    resolve_precedence,
    validate_config,
)


def test_valid_config_passes_validation():
    """Verify standard default configuration validates without error."""
    cfg = {
        "defaults": {
            "staleness_threshold_s": 0.1,
            "price_anomaly_stddev": 5.0,
            "price_window": 100,
            "dedup_cache_size": 100000,
            "circuit_filter_pct": 0.05,
        },
        "storage": {
            "wal_path": "data/wal",
            "max_segment_bytes": 67108864,
        },
        "network": {
            "host": "0.0.0.0",
            "port": 9876,
        },
    }
    validate_config(cfg)


def test_unknown_top_level_section_fails():
    """Verify unknown sections are rejected."""
    cfg = {
        "unknown_section": {"foo": "bar"},
    }
    with pytest.raises(ConfigValidationError, match="Unknown configuration section 'unknown_section'"):
        validate_config(cfg)


def test_unknown_key_in_defaults_fails():
    """Verify misspelled or unknown key in [defaults] is rejected."""
    cfg = {
        "defaults": {
            "staleness_threshold_s": 0.05,
            "spelling_mistake_key": 42,
        }
    }
    with pytest.raises(ConfigValidationError, match="Unknown key 'spelling_mistake_key' in \\[defaults\\]"):
        validate_config(cfg)


def test_invalid_numeric_bounds():
    """Verify non-positive or out-of-range bounds are rejected."""
    # Negative staleness
    with pytest.raises(ConfigValidationError, match="staleness_threshold_s must be > 0"):
        validate_config({"defaults": {"staleness_threshold_s": -0.5}})

    # Price window < 2
    with pytest.raises(ConfigValidationError, match="price_window must be integer >= 2"):
        validate_config({"defaults": {"price_window": 1}})

    # Circuit filter pct >= 1.0
    with pytest.raises(ConfigValidationError, match="circuit_filter_pct must be between 0 and 1.0"):
        validate_config({"defaults": {"circuit_filter_pct": 1.5}})

    # Invalid port > 65535
    with pytest.raises(ConfigValidationError, match="network.port must be integer between 1 and 65535"):
        validate_config({"network": {"port": 70000}})


def test_precedence_hierarchy(monkeypatch):
    """Verify resolution precedence: CLI > Env > Config > Default."""
    monkeypatch.setenv("TEST_ENV_VAR", "env_value")

    # 1. CLI wins over all
    res1 = resolve_precedence(
        cli_val="cli_value",
        env_var_name="TEST_ENV_VAR",
        config_val="config_value",
        default_val="default_value",
    )
    assert res1 == "cli_value"

    # 2. Env wins over Config and Default
    res2 = resolve_precedence(
        cli_val=None,
        env_var_name="TEST_ENV_VAR",
        config_val="config_value",
        default_val="default_value",
    )
    assert res2 == "env_value"

    # 3. Config wins over Default when Env is absent
    monkeypatch.delenv("TEST_ENV_VAR", raising=False)
    res3 = resolve_precedence(
        cli_val=None,
        env_var_name="TEST_ENV_VAR",
        config_val="config_value",
        default_val="default_value",
    )
    assert res3 == "config_value"

    # 4. Default is returned when all higher sources are absent
    res4 = resolve_precedence(
        cli_val=None,
        env_var_name="TEST_ENV_VAR",
        config_val=None,
        default_val="default_value",
    )
    assert res4 == "default_value"
