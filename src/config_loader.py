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

from quality import QualityConfig

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


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load configuration dictionary from mdrap.toml or fallback to defaults."""
    cfg_file = Path(path) if path else find_config_path()
    if not cfg_file or not cfg_file.is_file():
        return dict(DEFAULT_CONFIG)

    with open(cfg_file, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


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
    )
