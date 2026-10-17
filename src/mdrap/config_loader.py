"""MDRAP Hierarchical Configuration Loader.

Loads, resolves, and hashes `mdrap.toml` configurations using Python stdlib `tomllib`.
Resolution precedence ladder:
    defaults -> venue -> instrument_class -> instrument
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

from .quality import QualityConfig

__stability__ = "stable"

DEFAULT_CONFIG: dict[str, Any] = {
    "defaults": {
        "staleness_threshold_s": 0.05,
        "price_anomaly_stddev": 6.0,
        "price_window": 50,
        "dedup_cache_size": 200000,
        "circuit_filter_pct": 0.10,
    }
}


def find_config_path(start_path: str | Path | None = None) -> Path | None:
    """Search upwards for mdrap.toml."""
    cur = Path(start_path or os.getcwd()).resolve()
    for _ in range(5):
        cand = cur / "mdrap.toml"
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


class ConfigValidationError(ValueError):
    """Raised when configuration contains unknown keys, malformed sections, or invalid bounds."""
    pass


ALLOWED_TOP_SECTIONS = {"defaults", "venue", "system", "storage", "network", "security"}
ALLOWED_QUALITY_KEYS = {
    "staleness_threshold_s",
    "price_anomaly_stddev",
    "price_window",
    "dedup_cache_size",
    "circuit_filter_pct",
    "reorder_window_s",
    "reorder_max_slots",
}
ALLOWED_STORAGE_KEYS = {
    "wal_path",
    "db_path",
    "max_segment_bytes",
    "snapshot_interval_events",
    "sync_mode",
}
ALLOWED_NETWORK_KEYS = {
    "host",
    "port",
    "listen_backlog",
    "max_clients",
    "enable_shm",
    "shm_name",
}
ALLOWED_SECURITY_KEYS = {
    "rate_limit_per_sec",
    "rate_limit_capacity",
    "require_hmac",
    "audit_hash_algorithm",
    "enabled",
    "secret_key",
    "token_salt",
    "audit_log_path",
    "allowed_cidrs",
}
ALLOWED_SYSTEM_KEYS = {
    "log_level",
    "metrics_enabled",
    "clock_source",
    "worker_threads",
    "env",
}


def _validate_numeric_bounds(params: dict[str, Any], prefix: str = "defaults") -> None:
    if "staleness_threshold_s" in params:
        s = params["staleness_threshold_s"]
        if not isinstance(s, (int, float)) or s <= 0:
            raise ConfigValidationError(f"{prefix}.staleness_threshold_s must be > 0, got {s!r}")
    if "price_anomaly_stddev" in params:
        dev = params["price_anomaly_stddev"]
        if not isinstance(dev, (int, float)) or dev <= 0:
            raise ConfigValidationError(f"{prefix}.price_anomaly_stddev must be > 0, got {dev!r}")
    if "price_window" in params:
        w = params["price_window"]
        if not isinstance(w, int) or w < 2:
            raise ConfigValidationError(f"{prefix}.price_window must be integer >= 2, got {w!r}")
    if "dedup_cache_size" in params:
        c = params["dedup_cache_size"]
        if not isinstance(c, int) or c <= 0:
            raise ConfigValidationError(f"{prefix}.dedup_cache_size must be positive integer, got {c!r}")
    if "circuit_filter_pct" in params:
        pct = params["circuit_filter_pct"]
        if not isinstance(pct, (int, float)) or pct <= 0 or pct >= 1.0:
            raise ConfigValidationError(f"{prefix}.circuit_filter_pct must be between 0 and 1.0, got {pct!r}")
    if "reorder_window_s" in params:
        rw = params["reorder_window_s"]
        if not isinstance(rw, (int, float)) or rw < 0:
            raise ConfigValidationError(f"{prefix}.reorder_window_s must be >= 0, got {rw!r}")
    if "reorder_max_slots" in params:
        rms = params["reorder_max_slots"]
        if not isinstance(rms, int) or rms <= 0:
            raise ConfigValidationError(f"{prefix}.reorder_max_slots must be positive integer, got {rms!r}")


def validate_config(cfg: dict[str, Any]) -> None:
    """Validate configuration dictionary, failing closed on unknown keys or invalid bounds."""
    if not isinstance(cfg, dict):
        raise ConfigValidationError(f"Configuration root must be a dictionary, got {type(cfg).__name__}")

    for section, content in cfg.items():
        if section not in ALLOWED_TOP_SECTIONS:
            raise ConfigValidationError(
                f"Unknown configuration section '{section}'. Allowed: {sorted(ALLOWED_TOP_SECTIONS)}"
            )
        if not isinstance(content, dict):
            raise ConfigValidationError(f"Section '{section}' must be a table/dictionary, got {type(content).__name__}")

    # Validate defaults / quality parameters
    defaults = cfg.get("defaults", {})
    for k in defaults:
        if k not in ALLOWED_QUALITY_KEYS:
            raise ConfigValidationError(f"Unknown key '{k}' in [defaults]. Allowed: {sorted(ALLOWED_QUALITY_KEYS)}")
    _validate_numeric_bounds(defaults, prefix="defaults")

    # Validate storage
    if "storage" in cfg:
        storage = cfg["storage"]
        for k in storage:
            if k not in ALLOWED_STORAGE_KEYS:
                raise ConfigValidationError(f"Unknown key '{k}' in [storage]. Allowed: {sorted(ALLOWED_STORAGE_KEYS)}")
        if "max_segment_bytes" in storage:
            msb = storage["max_segment_bytes"]
            if not isinstance(msb, (int, float)) or msb <= 0:
                raise ConfigValidationError(f"storage.max_segment_bytes must be positive numeric, got {msb!r}")

    # Validate network
    if "network" in cfg:
        net = cfg["network"]
        for k in net:
            if k not in ALLOWED_NETWORK_KEYS:
                raise ConfigValidationError(f"Unknown key '{k}' in [network]. Allowed: {sorted(ALLOWED_NETWORK_KEYS)}")
        if "port" in net:
            p = net["port"]
            if not isinstance(p, int) or p < 1 or p > 65535:
                raise ConfigValidationError(f"network.port must be integer between 1 and 65535, got {p!r}")

    # Validate security
    if "security" in cfg:
        sec = cfg["security"]
        for k in sec:
            if k not in ALLOWED_SECURITY_KEYS:
                raise ConfigValidationError(f"Unknown key '{k}' in [security]. Allowed: {sorted(ALLOWED_SECURITY_KEYS)}")

    # Validate system
    if "system" in cfg:
        sys_sec = cfg["system"]
        for k in sys_sec:
            if k not in ALLOWED_SYSTEM_KEYS:
                raise ConfigValidationError(f"Unknown key '{k}' in [system]. Allowed: {sorted(ALLOWED_SYSTEM_KEYS)}")


# Backward-compatible alias
validate_config_schema = validate_config


def resolve_precedence(
    cli_val: Any = None,
    env_var_name: str | None = None,
    config_val: Any = None,
    default_val: Any = None,
) -> Any:
    """Enforce strict configuration precedence ladder: CLI > Environment > File > Default."""
    if cli_val is not None:
        return cli_val
    if env_var_name:
        env_val = os.environ.get(env_var_name)
        if env_val is not None and env_val != "":
            return env_val
    if config_val is not None:
        return config_val
    return default_val


def load_config(path: str | Path | None = None, validate: bool = True) -> dict[str, Any]:
    """Load configuration dictionary from mdrap.toml or fallback to defaults."""
    cfg_file = Path(path) if path else find_config_path()
    if not cfg_file or not cfg_file.is_file():
        cfg = dict(DEFAULT_CONFIG)
    else:
        with open(cfg_file, "rb") as f:
            raw = f.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        cfg = tomllib.loads(raw.decode("utf-8"))

    if validate:
        validate_config(cfg)
    return cfg


def compute_config_hash(cfg: dict[str, Any] | str | bytes | None = None) -> str:
    """Compute deterministic SHA-256 hash of configuration."""
    if cfg is None:
        cfg = load_config()
    if isinstance(cfg, dict):
        # Canonical JSON representation for stable hashing
        data = json.dumps(cfg, sort_keys=True).encode("utf-8")
    elif isinstance(cfg, str):
        data = cfg.encode("utf-8")
    else:
        data = cfg
    return hashlib.sha256(data).hexdigest()


def resolve_config(
    config: dict[str, Any],
    venue: str | None = None,
    instrument_class: str | None = None,
    symbol: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve configuration parameters with full origin layer provenance tracking.

    Returns:
        (resolved_params, origins_dict)
    """
    resolved: dict[str, Any] = {}
    origins: dict[str, str] = {}

    # 1. Base defaults
    defaults = config.get("defaults", {})
    for k, v in defaults.items():
        if not isinstance(v, dict):
            resolved[k] = v
            origins[k] = "defaults"

    if not venue:
        return resolved, origins

    venues = config.get("venue", {})
    v_conf = venues.get(venue, {})
    if not v_conf:
        return resolved, origins

    # 2. Venue-level overrides
    for k, v in v_conf.items():
        if not isinstance(v, dict):
            resolved[k] = v
            origins[k] = f"venue.{venue}"

    # 3. Instrument class overrides (e.g. crypto, equity)
    if instrument_class:
        cls_conf = v_conf.get("instrument_class", {}).get(instrument_class, {})
        for k, v in cls_conf.items():
            if not isinstance(v, dict):
                resolved[k] = v
                origins[k] = f"venue.{venue}.instrument_class.{instrument_class}"

    # 4. Instrument symbol overrides (e.g. BTCUSDT, AAPL)
    if symbol:
        inst_conf = v_conf.get("instrument", {}).get(symbol, {})
        for k, v in inst_conf.items():
            if not isinstance(v, dict):
                resolved[k] = v
                origins[k] = f"venue.{venue}.instrument.{symbol}"

    return resolved, origins


def to_quality_config(resolved: dict[str, Any]) -> QualityConfig:
    """Construct QualityConfig from resolved dictionary."""
    return QualityConfig(
        staleness_threshold_s=float(resolved.get("staleness_threshold_s", 0.05)),
        price_anomaly_stddev=float(resolved.get("price_anomaly_stddev", 6.0)),
        price_window=int(resolved.get("price_window", 50)),
        dedup_cache_size=int(resolved.get("dedup_cache_size", 200000)),
        reorder_window_s=float(resolved.get("reorder_window_s", 0.0)),
        reorder_max_slots=int(resolved.get("reorder_max_slots", 32)),
    )


def migrate_yaml_to_toml(
    yaml_path: str | Path | None = None,
    toml_path: str | Path | None = None,
) -> Path:
    """Migrate legacy config.yaml to mdrap.toml format (Phase 3 Task 3.5)."""
    if yaml_path is None:
        cand = Path("config.yaml")
        if not cand.is_file():
            cand = Path("config/config.yaml")
        yaml_path = cand
    else:
        yaml_path = Path(yaml_path)

    if not yaml_path.is_file():
        raise FileNotFoundError(f"Source YAML configuration not found: {yaml_path}")

    if toml_path is None:
        toml_path = Path("mdrap.toml")
    else:
        toml_path = Path(toml_path)

    raw_data: dict[str, Any] = {}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except ImportError:
        with open(yaml_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    try:
                        v = float(v) if "." in v else int(v)
                    except ValueError:
                        pass
                    raw_data[k] = v

    defaults_dict: dict[str, Any] = {}
    sections_dict: dict[str, dict[str, Any]] = {}

    for k, v in raw_data.items():
        if k in ("quality", "defaults"):
            if isinstance(v, dict):
                defaults_dict.update(v)
        elif k in ALLOWED_TOP_SECTIONS:
            if isinstance(v, dict):
                sections_dict.setdefault(k, {}).update(v)
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if sub_k in ALLOWED_QUALITY_KEYS:
                    defaults_dict[sub_k] = sub_v
        elif k in ALLOWED_QUALITY_KEYS:
            defaults_dict[k] = v

    toml_lines = [
        "# MDRAP Unified Configuration (Migrated from config.yaml)",
        "# Spec §26 & Phase 3 TOML Format",
        "",
        "[defaults]",
    ]
    for k, v in sorted(defaults_dict.items()):
        if isinstance(v, (int, float)):
            toml_lines.append(f"{k} = {v}")
        elif isinstance(v, bool):
            toml_lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, str):
            toml_lines.append(f'{k} = "{v}"')

    for sec, items in sorted(sections_dict.items()):
        if sec == "defaults" or not items:
            continue
        toml_lines.append(f"\n[{sec}]")
        for k, v in sorted(items.items()):
            if isinstance(v, (int, float)):
                toml_lines.append(f"{k} = {v}")
            elif isinstance(v, bool):
                toml_lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                toml_lines.append(f'{k} = "{v}"')

    with open(toml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(toml_lines) + "\n")

    return toml_path
