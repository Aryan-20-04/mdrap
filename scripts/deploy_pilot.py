"""MDRAP Production Deployment & Preflight Automation (Phase 5 Workstream A).

Executes automated preflight checks, directory initialization, configuration validation,
and smoke verification prior to starting the engine service.
Stdlib only.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.ingestlog import IngestLog
from mdrap.models import QualityStatus
from mdrap.quality import QualityEngine


def verify_prerequisites() -> Dict[str, bool]:
    """Validate system runtime prerequisites."""
    checks = {
        "python_version_ge_3_11": sys.version_info >= (3, 11),
        "64_bit_architecture": sys.maxsize > 2**32,
    }
    return checks


def setup_deployment_environment(base_dir: str) -> Dict[str, str]:
    """Create necessary operational directories with correct permissions."""
    wal_dir = os.path.join(base_dir, "wal")
    data_dir = os.path.join(base_dir, "data")
    logs_dir = os.path.join(base_dir, "logs")

    os.makedirs(wal_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    return {
        "wal_dir": wal_dir,
        "data_dir": data_dir,
        "logs_dir": logs_dir,
    }


def execute_smoke_verification(wal_dir: str) -> bool:
    """Run an isolated end-to-end smoke check against the deployment directory."""
    try:
        adapter = ReplayFeedAdapter(
            FeedAdapterConfig(venue="SMOKE_NASDAQ", feed_id="SMOKE_CH1"),
            frames=[{"seq": 1, "sym": "SPY", "px": 500.0, "sz": 100.0, "type": "TRADE"}],
        )
        adapter.connect()
        quality = QualityEngine()
        log = IngestLog(log_dir=wal_dir, max_segment_bytes=1024 * 1024)

        raw = adapter.poll()
        if raw is None:
            return False
        canon = adapter.normalize(raw)
        eval_res = quality.evaluate(canon)
        if eval_res.quality_status != QualityStatus.VALID:
            return False

        off = log.append(raw)
        log.flush()
        log.close()
        adapter.disconnect()
        return off >= 0
    except Exception as exc:
        print(f"[-] Smoke verification failed: {exc}", file=sys.stderr)
        return False


def run_deployment_preflight(base_dir: str = "deploy_staging") -> bool:
    """Run complete deployment automation procedure."""
    print("=== MDRAP Automated Deployment Preflight ===")
    checks = verify_prerequisites()
    for name, passed in checks.items():
        status = "OK" if passed else "FAIL"
        print(f"[*] Prerequisite check '{name}': {status}")
        if not passed:
            print(f"[-] Fatal: prerequisite '{name}' failed.", file=sys.stderr)
            return False

    paths = setup_deployment_environment(base_dir)
    print(f"[+] Initialized deployment paths under: {base_dir}")

    smoke_passed = execute_smoke_verification(paths["wal_dir"])
    if not smoke_passed:
        print("[-] Deployment smoke test failed.", file=sys.stderr)
        return False

    print("[+] Deployment preflight and smoke verification succeeded.")
    return True


if __name__ == "__main__":
    success = run_deployment_preflight("temp_pilot_deploy")
    sys.exit(0 if success else 1)
