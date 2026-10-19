#!/usr/bin/env python3
"""MDRAP Session Reset & Sequence Epoch Verification Probe (Item A2).

Empirically tests Start-of-Day (SOD) sequence reset behavior:
- Strict policy: flags unexpected sequence resets as OUT_OF_ORDER (failsafe DMA baseline).
- session_marker policy: advances epoch on session_id change -> 0 false alarms.
- auto_detect policy: detects overnight gap with sequence drop -> 0 false alarms.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from mdrap.clock import FixedClock
from mdrap.engine import Engine
from mdrap.models import QualityStatus, RawEvent


def run_session_probe() -> dict[str, int]:
    clock = FixedClock(1700000000.0)
    results = {}

    # 1. Strict Policy (Baseline)
    engine_strict = Engine(staleness_threshold_s=10.0, clock=clock, session_policy="strict")
    # Day 1: 200 sequential events
    day1_events = [
        RawEvent(
            source="NASDAQ",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0 + i * 0.01, "sequence": i},
            receive_timestamp=1700000000.0 + i,
        )
        for i in range(1, 201)
    ]
    engine_strict.submit(day1_events)

    # Day 2: Sequence resets to 1 after overnight gap
    day2_events = [
        RawEvent(
            source="NASDAQ",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 152.0 + i * 0.01, "sequence": i},
            receive_timestamp=1700000000.0 + 86400 + i,
        )
        for i in range(1, 201)
    ]
    decisions_strict = engine_strict.submit(day2_events)
    alarms_strict = sum(1 for d in decisions_strict if d.quality_status != QualityStatus.VALID)
    results["strict_alarms"] = alarms_strict

    # 2. Session Marker Policy
    engine_marker = Engine(staleness_threshold_s=10.0, clock=clock, session_policy="session_marker")
    day1_marker = [
        RawEvent(
            source="NASDAQ",
            session_id="DAY_1",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0 + i * 0.01, "sequence": i},
            receive_timestamp=1700000000.0 + i,
        )
        for i in range(1, 201)
    ]
    engine_marker.submit(day1_marker)

    day2_marker = [
        RawEvent(
            source="NASDAQ",
            session_id="DAY_2",
            payload={"instrument": "AAPL", "event_type": "TRADE", "price": 152.0 + i * 0.01, "sequence": i},
            receive_timestamp=1700000000.0 + 86400 + i,
        )
        for i in range(1, 201)
    ]
    decisions_marker = engine_marker.submit(day2_marker)
    alarms_marker = sum(1 for d in decisions_marker if d.quality_status != QualityStatus.VALID)
    results["session_marker_alarms"] = alarms_marker

    # 3. Auto-Detect Policy
    engine_auto = Engine(staleness_threshold_s=10.0, clock=clock, session_policy="auto_detect")
    engine_auto.submit(day1_events)
    decisions_auto = engine_auto.submit(day2_events)
    alarms_auto = sum(1 for d in decisions_auto if d.quality_status != QualityStatus.VALID)
    results["auto_detect_alarms"] = alarms_auto

    return results


def main():
    print("=" * 60)
    print("MDRAP Session Reset & Sequence Epoch Probe (Item A2)")
    print("=" * 60)
    results = run_session_probe()
    print(f"1. Strict Policy (SOD resets flagged):      {results['strict_alarms']} alarms")
    print(f"2. Session Marker Policy (epoch advanced): {results['session_marker_alarms']} alarms (Target: 0)")
    print(f"3. Auto-Detect Policy (overnight jump):    {results['auto_detect_alarms']} alarms (Target: 0)")

    if results["session_marker_alarms"] == 0 and results["auto_detect_alarms"] == 0:
        print("\n[PASS] Session reset epoch policies achieved 0 false alarms.")
        sys.exit(0)
    else:
        print("\n[FAIL] Session reset epoch policies generated unexpected alarms.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
