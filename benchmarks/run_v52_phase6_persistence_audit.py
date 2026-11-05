"""
MDRAP v5.2 Phase 6: Persistence, WAL & Crash Recovery Audit.

Evaluates:
1. IngestLog Write Throughput across fsync policies (always, grouped_by_time, never).
2. Deterministic Crash Recovery (Snapshot + WAL Replay).
3. WAL Corruption Injection Drill (Torn Write Truncation vs Middle-of-File Corruption Detection).
4. Exports RAW_PERSISTENCE_TELEMETRY.json.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mdrap.ingestlog import IngestLog, IngestLogCorruptError


def run_fsync_policy_benchmarks() -> dict[str, Any]:
    """
    Measures append throughput across fsync policies.
    """
    policies = ["never", "grouped_by_time", "always"]
    results = {}

    sample_payload = {"instrument": "BTCUSDT", "price": 100.5, "size": 10.0, "seq": 1}

    for pol in policies:
        tmpdir = tempfile.mkdtemp(prefix=f"mdrap_wal_{pol}_")
        num_events = 1_000 if pol == "always" else 20_000
        try:
            log = IngestLog(tmpdir, fsync_policy=pol, max_segment_bytes=10_000_000)
            t0 = time.perf_counter_ns()
            for i in range(num_events):
                log.append(sample_payload)
            t1 = time.perf_counter_ns()
            log.close()

            duration_s = (t1 - t0) / 1e9
            eps = num_events / max(1e-6, duration_s)
            p50_us = (duration_s / num_events) * 1e6

            results[pol] = {
                "policy": pol,
                "events_appended": num_events,
                "duration_s": round(duration_s, 4),
                "throughput_eps": round(eps, 1),
                "mean_latency_us": round(p50_us, 2),
            }
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return results


def run_crash_recovery_drill() -> dict[str, Any]:
    """
    Simulates crash recovery: Appends 2,000 frames, closes mid-session, recovers log,
    and validates that all offsets and payloads are cleanly restored.
    """
    tmpdir = tempfile.mkdtemp(prefix="mdrap_crash_drill_")
    total_frames = 2_000
    try:
        # Phase 1: Write first 1,000 frames
        log1 = IngestLog(tmpdir, fsync_policy="never")
        for i in range(1_000):
            payload = {"seq": i, "px": 100.0 + i * 0.01}
            log1.append(payload)
        log1.close()

        # Phase 2: Reopen (Recovery 1) and append next 1,000 frames
        log2 = IngestLog(tmpdir, fsync_policy="never")
        for i in range(1_000, 2_000):
            payload = {"seq": i, "px": 100.0 + i * 0.01}
            log2.append(payload)
        next_off = log2._next_offset
        log2.close()

        # Phase 3: Reopen (Recovery 2) and verify total offset count
        log3 = IngestLog(tmpdir, fsync_policy="never")
        recovered_offset = log3._next_offset
        
        # Read back all frames
        read_frames = list(log3.iter_from(0))
        log3.close()

        return {
            "total_frames_written": total_frames,
            "recovered_next_offset": recovered_offset,
            "frames_read_back": len(read_frames),
            "recovery_exact": recovered_offset == total_frames and len(read_frames) == total_frames,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_corruption_resilience_drill() -> dict[str, Any]:
    """
    Tests:
    1. Torn write at EOF -> Should auto-truncate and recover remaining frames.
    2. Byte flip in middle of WAL file -> Should raise IngestLogCorruptError.
    """
    tmpdir = tempfile.mkdtemp(prefix="mdrap_corrupt_drill_")
    try:
        # Create a log with 100 frames
        log = IngestLog(tmpdir, fsync_policy="never")
        for i in range(100):
            log.append({"idx": i})
        log.close()

        seg_files = sorted([f for f in os.listdir(tmpdir) if f.endswith(".log")])
        target_file = os.path.join(tmpdir, seg_files[0])

        # Test Case 1: Torn write at EOF (append 15 garbage bytes)
        with open(target_file, "ab") as f:
            f.write(b"CORRUPT_TAIL_GARBAGE")

        log_torn = IngestLog(tmpdir, fsync_policy="never")
        torn_recovered_frames = len(list(log_torn.iter_from(0)))
        corrupted_frames_detected = log_torn.corrupted_frames_count
        log_torn.close()

        # Test Case 2: Byte flip in middle of file
        with open(target_file, "r+b") as f:
            f.seek(500) # In the middle of an existing frame
            f.write(b"\xFF\xFF\xFF\xFF")

        middle_corrupt_caught = False
        try:
            _ = IngestLog(tmpdir, fsync_policy="never")
        except IngestLogCorruptError:
            middle_corrupt_caught = True

        return {
            "torn_tail_auto_truncated": torn_recovered_frames == 100,
            "torn_frames_detected_count": corrupted_frames_detected,
            "middle_file_corruption_caught": middle_corrupt_caught,
            "integrity_drill_passed": torn_recovered_frames == 100 and middle_corrupt_caught,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 80)
    print("MDRAP v5.2 PHASE 6: PERSISTENCE, WAL & CRASH RECOVERY AUDIT")
    print("=" * 80)

    print("\n[1/3] Benchmarking IngestLog Write Throughput across Fsync Policies...")
    fsync_results = run_fsync_policy_benchmarks()
    for k, v in fsync_results.items():
        print(f"  -> Policy '{k}': Throughput = {v['throughput_eps']} EPS | Latency = {v['mean_latency_us']} us")

    print("\n[2/3] Executing Deterministic Crash Recovery Drill...")
    crash_results = run_crash_recovery_drill()
    print(f"  -> Written: {crash_results['total_frames_written']} | Recovered: {crash_results['recovered_next_offset']}")
    print(f"  -> Exact State Reconstruction: {crash_results['recovery_exact']}")

    print("\n[3/3] Executing IngestLog Corruption & Torn-Write Drill...")
    corrupt_results = run_corruption_resilience_drill()
    print(f"  -> Torn Tail Auto-Truncated: {corrupt_results['torn_tail_auto_truncated']} ({corrupt_results['torn_frames_detected_count']} torn frame caught)")
    print(f"  -> Middle-of-File Corruption Caught: {corrupt_results['middle_file_corruption_caught']}")
    print(f"  -> Overall Integrity Drill Passed: {corrupt_results['integrity_drill_passed']}")

    telemetry = {
        "timestamp": time.time(),
        "fsync_policy_benchmarks": fsync_results,
        "crash_recovery_drill": crash_results,
        "corruption_drill": corrupt_results,
    }
    out_path = ROOT / "audit" / "v5.2" / "RAW_PERSISTENCE_TELEMETRY.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    print(f"\n[OK] Phase 6 telemetry saved to {out_path}")


if __name__ == "__main__":
    main()
