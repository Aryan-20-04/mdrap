"""MDRAP Run Manifest Generator.

Captures complete deterministic environment, configuration, hardware, and execution
metadata for auditability and experiment reproducibility.
"""

from __future__ import annotations

import datetime
import json
import platform
import subprocess
import sys
from typing import Any

from config_loader import compute_config_hash
from fastpath import HAS_FASTPATH

__stability__ = "stable"


def get_git_revision() -> str:
    """Obtain current git commit hash if running in a git repo."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def create_run_manifest(
    config_hash: str | None = None,
    seed: int | None = None,
    num_events: int = 0,
    elapsed_s: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create structured provenance manifest dictionary."""
    if config_hash is None:
        config_hash = compute_config_hash()

    eps = (num_events / elapsed_s) if elapsed_s > 0 else 0.0

    manifest = {
        "manifest_version": "1.0.0",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_commit": get_git_revision(),
        "config_sha256": config_hash,
        "active_engine_tier": "native_c" if HAS_FASTPATH else "pure_python",
        "python": {
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "compiler": platform.python_compiler(),
        },
        "system": {
            "os": sys.platform,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
        },
        "execution": {
            "argv": list(sys.argv),
            "seed": seed,
            "events_processed": num_events,
            "elapsed_seconds": round(elapsed_s, 6),
            "throughput_eps": round(eps, 2),
        },
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def write_run_manifest(
    manifest: dict[str, Any], filepath: str = "manifest.json"
) -> str:
    """Save manifest to JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return filepath
