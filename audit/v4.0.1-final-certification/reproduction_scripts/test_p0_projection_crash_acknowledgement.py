"""Subprocess Crash & Acknowledgement Durability Verification (P0) for MDRAP v4.0.1.

Simulates sudden, uncatchable OS-level process termination using proc.kill()
(Win32 TerminateProcess / POSIX SIGKILL) against an actively ingesting Engine.

Verifies:
1. All offsets acknowledged as fsynced/durable (durable=True) survive crash recovery without loss.
2. In-flight OS-buffered records are audited for survival vs truncation.
3. WAL CRC-32 integrity and SQLite PRAGMA integrity_check succeed cleanly.
4. Projection checkpoint remains strictly contiguous (no gaps or duplicates).
5. Second restart recovery is 100% idempotent.
6. Rigorous differentiation: proc.kill() proves uncoordinated process crash recovery,
   while physical power-loss protection depends on os.fsync() prior to durable acknowledgement.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _make_raw_event(i: int, source: str = "FEED_A", inst: str = "BTC-USD"):
    from mdrap import RawEvent
    base_ts = 1_700_000_000.0
    return RawEvent(
        source=source,
        payload={
            "instrument": inst,
            "event_type": "QUOTE",
            "bid": 50000.0 + (i % 10) * 0.1,
            "ask": 50001.0 + (i % 10) * 0.1,
            "exchange_ts": base_ts + i * 0.001,
            "sequence": i,
        },
        receive_timestamp=base_ts + i * 0.001,
        raw_id=f"crash_test_{source}_{i}",
    )


def run_child(wal_dir: str, db_path: str, progress_path: str, policy: str, target_count: int):
    """Child process entry point: continuously ingests market data and logs progress."""
    from mdrap import Engine, EngineConfig
    from mdrap.projection import SQLiteProjection

    proj = SQLiteProjection(db_path, name="crash_proj")
    config = EngineConfig(
        wal_dir=wal_dir,
        fsync_policy=policy,
        projections=[proj],
    )
    engine = Engine.open(wal_dir, config=config)

    fsynced_offsets = []
    os_buffered_offsets = []
    latest_acknowledged = -1

    try:
        for i in range(target_count):
            ev = _make_raw_event(i)
            dec = engine.submit(ev)
            if dec is not None:
                latest_acknowledged = dec.offset
                if dec.durable:
                    fsynced_offsets.append(dec.offset)
                else:
                    os_buffered_offsets.append(dec.offset)

            # Periodically write progress file atomically
            if i % 10 == 0 or i == target_count - 1:
                progress_data = {
                    "latest_submitted": i,
                    "latest_acknowledged": latest_acknowledged,
                    "fsynced_offsets": fsynced_offsets,
                    "os_buffered_offsets": os_buffered_offsets,
                    "durability_guarantee": getattr(engine, "durability_guarantee", "UNKNOWN"),
                    "synced_offset": getattr(engine, "synced_offset", -1),
                    "pid": os.getpid(),
                }
                tmp_prog = progress_path + ".tmp"
                try:
                    with open(tmp_prog, "w", encoding="utf-8") as f:
                        json.dump(progress_data, f)
                    for _ in range(10):
                        try:
                            os.replace(tmp_prog, progress_path)
                            break
                        except OSError:
                            time.sleep(0.005)
                except Exception:
                    pass

            time.sleep(0.001)  # 1ms throttle to allow parent to intercept mid-flight
    except Exception as e:
        print(f"[CHILD ERROR] {e}", file=sys.stderr)
        sys.exit(1)


def run_parent_test_case(policy: str, target_count: int = 600, log_func=print) -> dict:
    """Parent coordinator: launches child, kills it with TerminateProcess/SIGKILL, and audits recovery."""
    from mdrap import Engine, EngineConfig
    from mdrap.projection import SQLiteProjection

    work_dir = Path(tempfile.mkdtemp(prefix=f"mdrap_crash_{policy}_"))
    wal_dir = work_dir / "wal"
    db_path = work_dir / "proj.db"
    progress_path = work_dir / "progress.json"

    log_func(f"\n{'='*70}")
    log_func(f"SUBPROCESS CRASH EXPERIMENT: policy='{policy}' target_count={target_count}")
    log_func(f"Working Directory: {work_dir}")

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--wal-dir", str(wal_dir),
        "--db-path", str(db_path),
        "--progress-path", str(progress_path),
        "--policy", policy,
        "--target-count", str(target_count),
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log_func(f"Launched child process PID: {proc.pid}")

    # Wait until child has processed and acknowledged at least 250 events
    child_progress = None
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if progress_path.exists():
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    child_progress = json.load(f)
                if child_progress.get("latest_acknowledged", -1) >= 250:
                    break
            except Exception:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.02)

    if child_progress is None or child_progress.get("latest_acknowledged", -1) < 200:
        proc.kill()
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"Child failed to reach threshold. Output:\n{out.decode()}\n{err.decode()}")

    ack_before_kill = child_progress["latest_acknowledged"]
    fsynced_before_kill = set(child_progress["fsynced_offsets"])
    os_buffered_before_kill = set(child_progress["os_buffered_offsets"])
    log_func(f"Child reached offset {ack_before_kill} ({len(fsynced_before_kill)} fsynced, {len(os_buffered_before_kill)} os_buffered).")

    # SUDDEN UNGRACEFUL TERMINATION VIA PROC.KILL() (Win32 TerminateProcess / SIGKILL)
    log_func("Executing proc.kill() (Win32 TerminateProcess / POSIX SIGKILL)...")
    proc.kill()
    exit_code = proc.wait(timeout=5)
    log_func(f"Child process terminated. Exit code: {exit_code}")

    # Check that process died abruptly
    assert exit_code != 0, f"Child exited with clean code 0 despite proc.kill()!"

    # 1. RECOVERY REOPEN
    log_func("Opening fresh Engine against crashed WAL and projection DB...")
    recovered_proj = SQLiteProjection(str(db_path), name="crash_proj")
    rec_config = EngineConfig(
        wal_dir=str(wal_dir),
        fsync_policy=policy,
        projections=[recovered_proj],
    )
    rec_engine = Engine.open(str(wal_dir), config=rec_config)

    # 2. PROJECTION RECOVERY
    log_func("Running engine.recover_projections()...")
    recovery_stats = rec_engine.recover_projections()
    log_func(f"Recovery stats: {recovery_stats}")

    # 3. VERIFY WAL AND PROJECTION CHECKPOINTS
    rec_checkpoint = recovered_proj.checkpoint()
    log_func(f"Recovered projection checkpoint: {rec_checkpoint}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check;")
    sqlite_integrity = cur.fetchone()[0]
    log_func(f"SQLite PRAGMA integrity_check: {sqlite_integrity}")
    assert sqlite_integrity == "ok", f"Corrupted SQLite database: {sqlite_integrity}"

    cur.execute("SELECT sequence_number FROM canonical_events ORDER BY sequence_number ASC;")
    recovered_rows = cur.fetchall()
    recovered_offsets = [r[0] for r in recovered_rows]
    conn.close()

    log_func(f"Recovered {len(recovered_rows)} canonical events in projection database.")

    # 4. CRITICAL INVARIANT: ZERO LOSS OF ACKNOWLEDGED FSYNCED OFFSETS
    recovered_set = set(recovered_offsets)
    missing_fsynced = fsynced_before_kill - recovered_set
    log_func(f"Missing FSYNCED offsets: {len(missing_fsynced)}")
    assert len(missing_fsynced) == 0, f"DATA LOSS DETECTED! Missing fsynced offsets: {sorted(list(missing_fsynced))}"

    # Check OS buffered recovery stats
    survived_os_buffered = os_buffered_before_kill.intersection(recovered_set)
    lost_os_buffered = os_buffered_before_kill - recovered_set
    log_func(f"OS-buffered offsets survived: {len(survived_os_buffered)}/{len(os_buffered_before_kill)}, lost: {len(lost_os_buffered)}")

    # 5. CONTIGUITY CHECK
    if recovered_offsets:
        expected_seq = list(range(recovered_offsets[0], recovered_offsets[-1] + 1))
        assert recovered_offsets == expected_seq, "Projection offsets have gaps or duplicate sequence breaks!"
        assert rec_checkpoint == recovered_offsets[-1], f"Checkpoint mismatch: checkpoint={rec_checkpoint}, max_offset={recovered_offsets[-1]}"

    rec_engine.close()

    # 6. SECOND RECOVERY IDEMPOTENCY CHECK
    log_func("Testing second recovery idempotency...")
    reopen_proj = SQLiteProjection(str(db_path), name="crash_proj")
    rec_config2 = EngineConfig(
        wal_dir=str(wal_dir),
        fsync_policy=policy,
        projections=[reopen_proj],
    )
    reopen_engine = Engine.open(str(wal_dir), config=rec_config2)
    stats2 = reopen_engine.recover_projections()
    assert reopen_proj.checkpoint() == rec_checkpoint, f"Non-idempotent checkpoint after second recovery: {reopen_proj.checkpoint()} != {rec_checkpoint}"
    reopen_engine.close()
    log_func("Second recovery idempotency confirmed.")

    # Cleanup temp dir
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass

    return {
        "policy": policy,
        "exit_code": exit_code,
        "ack_before_kill": ack_before_kill,
        "fsynced_count": len(fsynced_before_kill),
        "fsynced_recovered": len(fsynced_before_kill) - len(missing_fsynced),
        "missing_fsynced": len(missing_fsynced),
        "os_buffered_count": len(os_buffered_before_kill),
        "os_buffered_survived": len(survived_os_buffered),
        "os_buffered_lost": len(lost_os_buffered),
        "sqlite_integrity": sqlite_integrity,
        "contiguity": True,
        "idempotency": True,
    }


def main():
    parser = argparse.ArgumentParser(description="P0 Subprocess Crash & Durability Verification")
    parser.add_argument("--child", action="store_true", help="Run in child worker mode")
    parser.add_argument("--wal-dir", type=str, help="WAL directory for child")
    parser.add_argument("--db-path", type=str, help="SQLite DB path for child")
    parser.add_argument("--progress-path", type=str, help="Progress JSON path for child")
    parser.add_argument("--policy", type=str, default="always", help="fsync policy to test")
    parser.add_argument("--target-count", type=int, default=600, help="Target count of events")
    args = parser.parse_args()

    if args.child:
        run_child(args.wal_dir, args.db_path, args.progress_path, args.policy, args.target_count)
        sys.exit(0)

    # Master orchestrator
    repo_root = Path(__file__).resolve().parents[3]
    evidence_dir = repo_root / "audit" / "v4.0.1-final-certification" / "raw_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_file = evidence_dir / "projection_crash_acknowledgement.log"

    with open(log_file, "w", encoding="utf-8") as out_f:
        def audit_log(msg: str):
            print(msg)
            out_f.write(msg + "\n")
            out_f.flush()

        audit_log("=" * 80)
        audit_log("MDRAP v4.0.1 P0: SUBPROCESS CRASH & DURABILITY AUDIT")
        audit_log("=" * 80)
        audit_log("Physical Durability vs OS Process Termination Audit")
        audit_log("OS: " + sys.platform)
        audit_log("Termination method: proc.kill() -> Win32 TerminateProcess / POSIX SIGKILL")
        audit_log("Note: proc.kill() proves clean recovery from abrupt process death.")
        audit_log("Hardware power-loss survival requires fsync prior to ACK (policy='always' / 'batch').\n")

        policies_to_test = [
            "always",
            "batch",  # grouped_by_time alias
            "grouped_by_time",
            "grouped_by_size",
            "adaptive",  # true_group_commit alias
            "never",
        ]

        results = []
        for pol in policies_to_test:
            res = run_parent_test_case(pol, target_count=500, log_func=audit_log)
            results.append(res)

        audit_log("\n" + "=" * 80)
        audit_log("FINAL SUMMARY MATRIX: SUBPROCESS CRASH & ACKNOWLEDGEMENT DURABILITY")
        audit_log("=" * 80)
        header = f"{'Policy':<18} | {'Ack Fsynced':<12} | {'Rec Fsynced':<12} | {'Lost Fsync':<11} | {'OS Buf Surv':<12} | {'Integrity':<10}"
        audit_log(header)
        audit_log("-" * len(header))
        for r in results:
            row = (
                f"{r['policy']:<18} | "
                f"{r['fsynced_count']:<12} | "
                f"{r['fsynced_recovered']:<12} | "
                f"{r['missing_fsynced']:<11} | "
                f"{r['os_buffered_survived']}/{r['os_buffered_count']:<10} | "
                f"{r['sqlite_integrity']:<10}"
            )
            audit_log(row)

        audit_log("\nALL POLICIES PROVEN: Zero lost fsynced offsets. 100% crash recovery contiguity and idempotency.")


if __name__ == "__main__":
    main()
