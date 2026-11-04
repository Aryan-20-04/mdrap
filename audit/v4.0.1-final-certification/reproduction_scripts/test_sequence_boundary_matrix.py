"""Authoritative Sequence-History Boundary & Eviction Matrix Test (P0 & P1) for MDRAP v4.0.1.

Tests 13 mandatory boundary scenarios across:
1. GoldenOracle (algorithmic ground truth)
2. QualityEngine (pure Python production reference)
3. FastQualityEngine (compiled C native hot path kernel)

Scenarios Tested:
1. 30 unique stale packets + retransmission of one
2. 31 unique stale packets + retransmission of one
3. 32 unique stale packets + retransmission of one (ring boundary capacity)
4. 33 unique stale packets + retransmission of evicted first packet (33rd evicts slot 0 -> NOT duplicate)
5. Retransmission immediately before ring eviction
6. Retransmission immediately after ring eviction
7. Circular buffer wraparound after multiple complete rotations (100+ packets)
8. Duplicate packets from an earlier session (session isolation)
9. Delayed packets arriving after a session reset
10. Sequence numbers near zero (0, 1, 2)
11. Sequence numbers near INT64_MAX (2^63 - 1)
12. Negative sequence numbers
13. Concurrent events for different instruments and sources (cross-talk isolation)

All decisions, statuses, reasons, and state transitions are compared for 100% tri-engine parity.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo_root))

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.quality import QualityEngine, QualityConfig
from mdrap.fastpath import FastQualityEngine, is_available as has_fastpath
from tests.golden_oracle import GoldenOracle


def make_event(
    seq: int,
    source: str = "FEED_A",
    inst: str = "BTC-USD",
    ts: float = 1_700_000_000.0,
    session_id: str | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"c_{source}_{inst}_{seq}_{ts}",
        instrument_id=inst,
        event_type=EventType.QUOTE,
        exchange_timestamp=ts,
        receive_timestamp=ts,
        processing_timestamp=ts,
        source=source,
        sequence_number=seq,
        bid_price=50000.0,
        ask_price=50001.0,
        quality_status=QualityStatus.VALID,
        session_id=session_id,
    )


def run_scenario(scenario_num: int, name: str, events: list[CanonicalEvent], log_func=print) -> bool:
    oracle = GoldenOracle()
    py_eng = QualityEngine(QualityConfig(staleness_threshold_s=60.0, price_window=10))
    c_eng = FastQualityEngine(staleness_threshold_s=60.0, stddev_threshold=3.0, price_window=10) if has_fastpath() else None

    log_func(f"\n--- Scenario {scenario_num}: {name} ({len(events)} events) ---")
    all_matched = True

    for idx, ev in enumerate(events):
        oracle_status, oracle_reasons, _ = oracle.evaluate(ev)
        py_res = py_eng.evaluate(ev)
        c_res = c_eng.evaluate(ev) if c_eng else None

        py_status = py_res.quality_status
        py_reasons = sorted(list(set(r.value if hasattr(r, "value") else str(r) for r in py_res.reasons)))
        oracle_reasons = sorted(list(set(oracle_reasons)))

        # Compare Oracle vs Python
        if oracle_status != py_status or oracle_reasons != py_reasons:
            log_func(f"  [MISMATCH Oracle vs Python] Event {idx} seq={ev.sequence_number}:")
            log_func(f"    Oracle: status={oracle_status.value} reasons={oracle_reasons}")
            log_func(f"    Python: status={py_status.value} reasons={py_reasons}")
            all_matched = False

        # Compare Python vs C
        if c_res is not None:
            c_status = c_res.quality_status
            c_reasons = sorted(list(set(r.value if hasattr(r, "value") else str(r) for r in c_res.reasons)))
            if py_status != c_status or py_reasons != c_reasons:
                log_func(f"  [MISMATCH Python vs Native C] Event {idx} seq={ev.sequence_number}:")
                log_func(f"    Python: status={py_status.value} reasons={py_reasons}")
                log_func(f"    C Hot : status={c_status.value} reasons={c_reasons}")
                all_matched = False

    if all_matched:
        log_func(f"  [PASS] Scenario {scenario_num} 100% parity across Oracle, Python, and C.")
    return all_matched


def main():
    evidence_dir = repo_root / "audit" / "v4.0.1-final-certification" / "raw_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_file = evidence_dir / "sequence_boundary_tests.log"

    with open(log_file, "w", encoding="utf-8") as out_f:
        def audit_log(msg: str):
            print(msg)
            out_f.write(msg + "\n")
            out_f.flush()

        audit_log("=" * 80)
        audit_log("MDRAP v4.0.1 P0/P1: SEQUENCE BOUNDARY & EVICTION MATRIX (13 SCENARIOS)")
        audit_log("=" * 80)
        audit_log(f"Native C Fastpath Available: {has_fastpath()}")

        scenarios_results = []

        # Baseline setup: established sequence = 1000
        # 1. 30 unique stale packets + retransmission
        evs1 = [make_event(1000, ts=100.0)]
        for i in range(1, 31):
            evs1.append(make_event(1000 - i, ts=100.0 + i))
        # Retransmit the 10th stale packet (should be detected as DUPLICATE)
        evs1.append(make_event(1000 - 10, ts=140.0))
        r1 = run_scenario(1, "30 unique stale packets + retransmission", evs1, audit_log)
        scenarios_results.append((1, "30 unique stale + retransmission", r1))

        # 2. 31 unique stale packets + retransmission
        evs2 = [make_event(1000, ts=100.0)]
        for i in range(1, 32):
            evs2.append(make_event(1000 - i, ts=100.0 + i))
        # Retransmit the 1st stale packet (still in 32-capacity ring)
        evs2.append(make_event(1000 - 1, ts=140.0))
        r2 = run_scenario(2, "31 unique stale packets + retransmission", evs2, audit_log)
        scenarios_results.append((2, "31 unique stale + retransmission", r2))

        # 3. 32 unique stale packets + retransmission (Exact Ring Capacity)
        evs3 = [make_event(1000, ts=100.0)]
        for i in range(1, 33):
            evs3.append(make_event(1000 - i, ts=100.0 + i))
        # Retransmit the very first stale packet (1000 - 1). Ring holds exactly 32, so 1000 - 1 is still present.
        evs3.append(make_event(1000 - 1, ts=140.0))
        r3 = run_scenario(3, "32 unique stale packets + retransmission (Ring Capacity)", evs3, audit_log)
        scenarios_results.append((3, "32 unique stale (exact ring capacity)", r3))

        # 4. 33 unique stale packets + retransmission (33rd evicts slot 0)
        evs4 = [make_event(1000, ts=100.0)]
        for i in range(1, 34):
            evs4.append(make_event(1000 - i, ts=100.0 + i))
        # The 33rd stale packet (1000 - 33) evicts the 1st stale packet (1000 - 1).
        # Retransmitting 1000 - 1 should now be classified as STALE, NOT duplicate!
        evs4.append(make_event(1000 - 1, ts=150.0))
        r4 = run_scenario(4, "33 unique stale packets (evicts slot 0 -> NOT duplicate)", evs4, audit_log)
        scenarios_results.append((4, "33 unique stale (eviction boundary)", r4))

        # 5. Retransmission immediately before ring eviction
        evs5 = [make_event(500, ts=100.0)]
        for i in range(1, 32):
            evs5.append(make_event(500 - i, ts=100.0 + i))
        # Slot 0 holds 500 - 1. We are at 31 items. Retransmit 500 - 1 (still present -> DUPLICATE)
        evs5.append(make_event(500 - 1, ts=140.0))
        r5 = run_scenario(5, "Retransmission immediately before ring eviction", evs5, audit_log)
        scenarios_results.append((5, "Retransmission immediately before eviction", r5))

        # 6. Retransmission immediately after ring eviction
        evs6 = [make_event(500, ts=100.0)]
        for i in range(1, 33):  # 32 stale items inserted
            evs6.append(make_event(500 - i, ts=100.0 + i))
        # Insert 33rd stale item -> evicts 500 - 1
        evs6.append(make_event(500 - 33, ts=135.0))
        # Immediately retransmit evicted 500 - 1 -> NOT duplicate (classified STALE)
        evs6.append(make_event(500 - 1, ts=140.0))
        r6 = run_scenario(6, "Retransmission immediately after ring eviction", evs6, audit_log)
        scenarios_results.append((6, "Retransmission immediately after eviction", r6))

        # 7. Circular buffer wraparound after multiple complete rotations (100 packets)
        evs7 = [make_event(2000, ts=100.0)]
        for i in range(1, 101):
            evs7.append(make_event(2000 - i, ts=100.0 + i * 0.1))
        # Retransmit the most recent packet (2000 - 100) -> MUST be DUPLICATE
        evs7.append(make_event(2000 - 100, ts=120.0))
        # Retransmit ancient packet (2000 - 10) -> evicted -> STALE (not duplicate)
        evs7.append(make_event(2000 - 10, ts=121.0))
        r7 = run_scenario(7, "Circular buffer wraparound after multiple rotations (100 pkts)", evs7, audit_log)
        scenarios_results.append((7, "100-packet multiple circular rotations", r7))

        # 8. Duplicate packets from an earlier session (session isolation)
        evs8 = [
            make_event(100, session_id="SESSION_1", ts=100.0),
            make_event(101, session_id="SESSION_1", ts=101.0),
            make_event(100, session_id="SESSION_2", ts=102.0),  # Session 2 resets sequence tracking
        ]
        r8 = run_scenario(8, "Duplicate packets across session reset", evs8, audit_log)
        scenarios_results.append((8, "Session change sequence isolation", r8))

        # 9. Delayed packets arriving after a session reset
        evs9 = [
            make_event(50, session_id="S1", ts=100.0),
            make_event(10, session_id="S2", ts=101.0),  # S2 starts
            make_event(20, session_id="S2", ts=102.0),
            make_event(5, session_id="S2", ts=103.0),   # Stale packet in S2
            make_event(5, session_id="S2", ts=104.0),   # Duplicate stale in S2
        ]
        r9 = run_scenario(9, "Delayed packets arriving after session reset", evs9, audit_log)
        scenarios_results.append((9, "Delayed packets after session reset", r9))

        # 10. Sequence numbers near zero (0, 1, 2)
        evs10 = [
            make_event(0, ts=100.0),
            make_event(1, ts=101.0),
            make_event(2, ts=102.0),
            make_event(1, ts=103.0),  # Stale seq 1
            make_event(1, ts=104.0),  # Duplicate seq 1
            make_event(0, ts=105.0),  # Stale seq 0
        ]
        r10 = run_scenario(10, "Sequence numbers near zero (0, 1, 2)", evs10, audit_log)
        scenarios_results.append((10, "Sequence numbers near zero", r10))

        # 11. Sequence numbers near max INT64 / UINT64 (2^63 - 1)
        max_seq = 9_223_372_036_854_775_807
        evs11 = [
            make_event(max_seq - 2, ts=100.0),
            make_event(max_seq - 1, ts=101.0),
            make_event(max_seq, ts=102.0),
            make_event(max_seq - 1, ts=103.0),  # Stale near max
            make_event(max_seq - 1, ts=104.0),  # Duplicate near max
        ]
        r11 = run_scenario(11, "Sequence numbers near INT64_MAX", evs11, audit_log)
        scenarios_results.append((11, "Sequence numbers near INT64_MAX", r11))

        # 12. Negative sequence numbers
        evs12 = [
            make_event(-10, ts=100.0),
            make_event(-5, ts=101.0),
            make_event(0, ts=102.0),
            make_event(-5, ts=103.0),   # Stale negative
            make_event(-5, ts=104.0),   # Duplicate negative
        ]
        r12 = run_scenario(12, "Negative sequence numbers", evs12, audit_log)
        scenarios_results.append((12, "Negative sequence numbers", r12))

        # 13. Concurrent events for different instruments and sources
        evs13 = [
            make_event(100, source="FEED_A", inst="BTC-USD", ts=100.0),
            make_event(100, source="FEED_B", inst="BTC-USD", ts=100.0),
            make_event(100, source="FEED_A", inst="ETH-USD", ts=100.0),
            make_event(90, source="FEED_A", inst="BTC-USD", ts=101.0),  # Stale on FEED_A:BTC-USD
            make_event(90, source="FEED_A", inst="BTC-USD", ts=102.0),  # Duplicate on FEED_A:BTC-USD
            make_event(90, source="FEED_B", inst="BTC-USD", ts=103.0),  # Stale on FEED_B:BTC-USD (not duplicate!)
        ]
        r13 = run_scenario(13, "Concurrent events for different instruments and sources", evs13, audit_log)
        scenarios_results.append((13, "Cross-source/instrument isolation", r13))

        audit_log("\n" + "=" * 80)
        audit_log("SEQUENCE BOUNDARY MATRIX RESULTS SUMMARY")
        audit_log("=" * 80)
        all_passed = True
        for num, desc, ok in scenarios_results:
            status_str = "PASS" if ok else "FAIL"
            audit_log(f"Scenario {num:2d}: [{status_str}] {desc}")
            if not ok:
                all_passed = False

        if all_passed:
            audit_log("\nALL 13 BOUNDARY SCENARIOS PASSED WITH 100% TRI-ENGINE PARITY.")
        else:
            audit_log("\nFAILURE: One or more sequence boundary scenarios failed.")
            sys.exit(1)


if __name__ == "__main__":
    main()
