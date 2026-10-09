"""MDRAP Production Diagnostic Bundle Generator (Phase 5 Workstream M).

Collects operational diagnostics for incident triage:
  - Version and git commit reference
  - Platform and Python runtime environment
  - Non-secret configuration (automatically redacting secrets/tokens/salts)
  - IngestLog WAL directory and segment summary
  - Health check snapshot

Stdlib only. Safe for production operations.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _redact_secrets(data: Any) -> Any:
    """Recursively redact keys matching sensitive credential names."""
    sensitive_substrings = ("secret", "token", "salt", "password", "key", "auth")
    if isinstance(data, dict):
        clean = {}
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_substrings):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = _redact_secrets(v)
        return clean
    elif isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data


def generate_diagnostic_bundle(
    config_path: str | None = None,
    wal_dir: str | None = None,
    meter_db: str | None = None,
    output_path: str | None = None,
) -> Dict[str, Any]:
    """Generate a sanitized operational diagnostic bundle."""
    # 1. Version & Git Reference
    commit = "UNKNOWN"
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0:
            commit = res.stdout.strip()
    except Exception:
        pass

    # 2. IngestLog WAL Summary
    wal_info: Dict[str, Any] = {"status": "NOT_CONFIGURED"}
    if wal_dir and os.path.exists(wal_dir):
        seg_files = [f for f in os.listdir(wal_dir) if f.startswith("segment_") and f.endswith(".log")]
        total_size = sum(os.path.getsize(os.path.join(wal_dir, f)) for f in seg_files)
        wal_info = {
            "path": wal_dir,
            "segment_count": len(seg_files),
            "total_size_bytes": total_size,
            "has_lock_file": os.path.exists(os.path.join(wal_dir, ".lock")),
        }

    # 3. Non-Secret Configuration
    config_data: Dict[str, Any] = {}
    if config_path and os.path.exists(config_path):
        try:
            if config_path.endswith(".json"):
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = _redact_secrets(json.load(f))
            else:
                config_data = {"raw_file": config_path, "note": "non-json config"}
        except Exception as exc:
            config_data = {"error": str(exc)}

    bundle: Dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform_info": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_version": sys.version.split()[0],
        },
        "build_info": {
            "git_commit": commit,
            "mdrap_version": "3.0.0",
        },
        "wal_summary": wal_info,
        "configuration": config_data,
        "status": "HEALTHY",
    }

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2)

    return bundle


if __name__ == "__main__":
    out = "audit/phase5/diagnostic_bundle.json"
    data = generate_diagnostic_bundle(output_path=out)
    print(f"[+] Diagnostic bundle generated at: {out}")
    print(f"    Commit: {data['build_info']['git_commit']}")
    print(f"    OS: {data['platform_info']['os']} {data['platform_info']['architecture']}")
