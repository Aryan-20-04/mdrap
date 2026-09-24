"""MDRAP Benchmark Regression Protection Guard (Phase 20).

Compares current benchmark run against historical release baseline.
Thresholds:
- Throughput degradation > 10% -> FAIL
- p99 latency increase > 15% -> FAIL
- Peak RSS increase > 20% -> FAIL
"""

import json
import os
import sys
from typing import Any


def check_regression(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    """Evaluates performance metrics against regression safety thresholds."""
    violations = []

    # 1. Throughput check (lower is worse)
    curr_eps = current["throughput"]["events_per_second"]
    base_eps = baseline["throughput"]["events_per_second"]
    eps_drop_pct = ((base_eps - curr_eps) / base_eps) * 100.0
    if eps_drop_pct > 10.0:
        violations.append(
            f"Throughput regression: current {curr_eps} eps vs baseline {base_eps} eps (-{eps_drop_pct:.1f}% > 10%)"
        )

    # 2. p99 Latency check (higher is worse)
    curr_p99 = current["latency_e2e_us"]["p99"]
    base_p99 = baseline["latency_e2e_us"]["p99"]
    if base_p99 > 0:
        lat_increase_pct = ((curr_p99 - base_p99) / base_p99) * 100.0
        if lat_increase_pct > 15.0:
            violations.append(
                f"p99 latency regression: current {curr_p99} us vs baseline {base_p99} us (+{lat_increase_pct:.1f}% > 15%)"
            )

    # 3. Peak RSS memory check
    curr_mem = current["resources"]["rss_mb"]
    base_mem = baseline["resources"]["rss_mb"]
    if base_mem > 0:
        mem_increase_pct = ((curr_mem - base_mem) / base_mem) * 100.0
        if mem_increase_pct > 20.0:
            violations.append(
                f"Memory RSS regression: current {curr_mem} MB vs baseline {base_mem} MB (+{mem_increase_pct:.1f}% > 20%)"
            )

    # 4. Zero Data Loss Invariant Check
    if current.get("invariants", {}).get("dropped_events", 0) > 0:
        violations.append(
            f"Zero data loss violated: {current['invariants']['dropped_events']} events dropped!"
        )

    passed = len(violations) == 0
    return passed, violations


if __name__ == "__main__":
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    curr_path = os.path.join(results_dir, "v1.0.0.json")
    base_path = os.path.join(results_dir, "baseline.json")

    if not os.path.exists(curr_path):
        print(f"[ERROR] Current benchmark file not found: {curr_path}")
        sys.exit(1)

    if not os.path.exists(base_path):
        # Establish current run as baseline if baseline doesn't exist yet
        import shutil
        shutil.copyfile(curr_path, base_path)
        print(f"[INIT] Established {curr_path} as new baseline: {base_path}")
        sys.exit(0)

    with open(curr_path, "r", encoding="utf-8") as f:
        curr_data = json.load(f)
    with open(base_path, "r", encoding="utf-8") as f:
        base_data = json.load(f)

    ok, errors = check_regression(curr_data, base_data)
    if not ok:
        print("[FAIL] Performance regression guard failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"[OK] Performance regression check PASSED (0 regressions detected vs baseline).")
        sys.exit(0)
