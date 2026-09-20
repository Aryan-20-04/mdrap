"""
MDRAP Central Platform Configuration Manager.

Loads externalized configuration for quality thresholds, anomaly windows,
watchdog timers, storage parameters, and security policies from config.yaml.

Adheres to Design Principle 1 & 8: Correctness, explicit tunability,
and zero mandatory runtime dependencies (graceful fallback if PyYAML absent).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@dataclass
class QualityConfig:
    staleness_threshold_s: float = 0.05
    price_anomaly_stddev: float = 6.0
    price_window: int = 50
    price_min_samples: int = 20
    price_reseed_after: int = 8
    price_sigma_floor_rel: float = 2e-4
    price_reseed_band_rel: float = 0.01
    max_future_skew_s: float = 1.0
    seq_jump_limit: int = 1 << 24
    dedup_cache_size: int = 200_000
    allow_negative: bool = False
    unseq_dup_status: str = "SUSPICIOUS"
    asset_classes: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def for_instrument(self, instrument_id: str) -> "QualityConfig":
        """Return a tailored QualityConfig instance with asset-class overrides applied."""
        inst = instrument_id.upper()
        # Detect asset class
        if any(c in inst for c in ("BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "USDT")):
            asset_type = "crypto"
        elif any(
            c in inst
            for c in (
                "AAPL",
                "MSFT",
                "NVDA",
                "TSLA",
                "AMZN",
                "GOOGL",
                "META",
                "SPY",
                "QQQ",
            )
        ):
            asset_type = "equities"
        else:
            asset_type = "default"

        overrides = self.asset_classes.get(asset_type, {})
        if not overrides:
            return self

        return QualityConfig(
            staleness_threshold_s=float(overrides.get(
                "staleness_threshold_s", self.staleness_threshold_s
            )),
            price_anomaly_stddev=float(overrides.get(
                "price_anomaly_stddev", self.price_anomaly_stddev
            )),
            price_window=int(overrides.get("price_window", self.price_window)),
            price_min_samples=int(overrides.get("price_min_samples", self.price_min_samples)),
            price_reseed_after=int(overrides.get("price_reseed_after", self.price_reseed_after)),
            price_sigma_floor_rel=float(overrides.get("price_sigma_floor_rel", self.price_sigma_floor_rel)),
            price_reseed_band_rel=float(overrides.get("price_reseed_band_rel", self.price_reseed_band_rel)),
            max_future_skew_s=float(overrides.get("max_future_skew_s", self.max_future_skew_s)),
            seq_jump_limit=int(overrides.get("seq_jump_limit", self.seq_jump_limit)),
            dedup_cache_size=int(
                overrides.get("dedup_cache_size", self.dedup_cache_size)
            ),
            allow_negative=bool(overrides.get("allow_negative", self.allow_negative)),
            unseq_dup_status=str(overrides.get("unseq_dup_status", self.unseq_dup_status)),
            asset_classes=self.asset_classes,
        )


@dataclass
class BBOConfig:
    quote_ttl_s: float = 2.0


@dataclass
class WatchdogConfig:
    silence_threshold_s: float = 2.0
    degradation_threshold: float = 0.90
    recovery_threshold: float = 0.93


@dataclass
class StorageConfig:
    batch_size: int = 500
    wal_mode: bool = True
    synchronous: str = "NORMAL"


@dataclass
class SecurityConfig:
    rate_limit_per_sec: float = 20_000.0
    rate_limit_capacity: float = 40_000.0
    require_hmac: bool = False
    audit_hash_algorithm: str = "sha256"


@dataclass
class DaemonConfig:
    host: str = "127.0.0.1"
    port: int = 9876
    require_auth: bool = False
    auth_token: str = ""


@dataclass
class FeedsConfig:
    default_provider: str = "crypto"
    mock_mode: bool = False
    max_queue_size: int = 50000
    polygon: Dict[str, Any] = field(default_factory=dict)
    databento: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ColumnarConfig:
    enabled: bool = True
    db_path: str = "data/mdrap.duckdb"
    parquet_dir: str = "data/parquet"
    threads: int = 4
    memory_limit: str = "2GB"


@dataclass
class PlatformConfig:
    quality: QualityConfig = field(default_factory=QualityConfig)
    bbo: BBOConfig = field(default_factory=BBOConfig)
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    feeds: FeedsConfig = field(default_factory=FeedsConfig)
    columnar: ColumnarConfig = field(default_factory=ColumnarConfig)


def _simple_yaml_parse(text: str) -> dict:
    """Lightweight stdlib fallback parser for nested YAML configs."""
    root: dict = {}
    # stack of (indent_level, dict_ref)
    stack = [(-1, root)]

    for line in text.splitlines():
        line = line.split("#")[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            # Pop stack until we find parent with strictly smaller indent
            while len(stack) > 1 and stack[-1][0] >= indent:
                stack.pop()

            parent_dict = stack[-1][1]

            if val:
                parent_dict[key] = _parse_val(val)
            else:
                new_dict: dict = {}
                parent_dict[key] = new_dict
                stack.append((indent, new_dict))

    return root


def _parse_val(v: str) -> Any:
    """Convert YAML string scalar to Python type."""
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    try:
        if "." in v or "e" in v.lower():
            return float(v)
        return int(v)
    except ValueError:
        return v.strip("\"'")


def load_config(config_path: Optional[str] = None) -> PlatformConfig:
    """
    Load platform configuration from config.yaml or return defaults.
    Searches config_path, then current directory, then project root.
    """
    candidates = []
    if config_path:
        candidates.append(config_path)
    candidates.extend(
        [
            "config.yaml",
            os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
        ]
    )

    raw_dict: dict = {}
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                if _HAS_YAML:
                    raw_dict = yaml.safe_load(content) or {}
                else:
                    raw_dict = _simple_yaml_parse(content)
                break
            except Exception as exc:
                print(
                    f"[mdrap WARNING] Failed to parse config file '{p}': {exc}. Using defaults.",
                    file=sys.stderr,
                )
                continue

    # 1. Quality
    q_data = raw_dict.get("quality", {})
    quality_cfg = QualityConfig(
        staleness_threshold_s=float(q_data.get("staleness_threshold_s", 0.05)),
        price_anomaly_stddev=float(q_data.get("price_anomaly_stddev", 6.0)),
        price_window=int(q_data.get("price_window", 50)),
        dedup_cache_size=int(q_data.get("dedup_cache_size", 200_000)),
        asset_classes=q_data.get("asset_classes", {}),
    )

    # 2. BBO
    b_data = raw_dict.get("bbo", {})
    bbo_cfg = BBOConfig(
        quote_ttl_s=float(b_data.get("quote_ttl_s", 2.0)),
    )

    # 3. Watchdog
    w_data = raw_dict.get("watchdog", {})
    watchdog_cfg = WatchdogConfig(
        silence_threshold_s=float(w_data.get("silence_threshold_s", 2.0)),
        degradation_threshold=float(w_data.get("degradation_threshold", 0.90)),
        recovery_threshold=float(w_data.get("recovery_threshold", 0.93)),
    )

    # 4. Storage
    s_data = raw_dict.get("storage", {})
    storage_cfg = StorageConfig(
        batch_size=int(s_data.get("batch_size", 500)),
        wal_mode=bool(s_data.get("wal_mode", True)),
        synchronous=str(s_data.get("synchronous", "NORMAL")),
    )

    # 5. Security
    sec_data = raw_dict.get("security", {})
    security_cfg = SecurityConfig(
        rate_limit_per_sec=float(sec_data.get("rate_limit_per_sec", 20_000.0)),
        rate_limit_capacity=float(sec_data.get("rate_limit_capacity", 40_000.0)),
        require_hmac=bool(sec_data.get("require_hmac", False)),
        audit_hash_algorithm=str(sec_data.get("audit_hash_algorithm", "sha256")),
    )

    # 6. Daemon
    d_data = raw_dict.get("daemon", {})
    env_token = os.environ.get("MDRAP_DAEMON_TOKEN", "")
    daemon_cfg = DaemonConfig(
        host=str(d_data.get("host", "127.0.0.1")),
        port=int(d_data.get("port", 9876)),
        require_auth=bool(d_data.get("require_auth", False) or bool(env_token)),
        auth_token=env_token or str(d_data.get("auth_token", "")),
    )

    # 7. Feeds
    f_data = raw_dict.get("feeds", {})
    feeds_cfg = FeedsConfig(
        default_provider=str(f_data.get("default_provider", "crypto")),
        mock_mode=bool(f_data.get("mock_mode", False)),
        max_queue_size=int(f_data.get("max_queue_size", 50000)),
        polygon=dict(f_data.get("polygon", {})),
        databento=dict(f_data.get("databento", {})),
    )

    return PlatformConfig(
        quality=quality_cfg,
        bbo=bbo_cfg,
        watchdog=watchdog_cfg,
        storage=storage_cfg,
        security=security_cfg,
        daemon=daemon_cfg,
        feeds=feeds_cfg,
    )
