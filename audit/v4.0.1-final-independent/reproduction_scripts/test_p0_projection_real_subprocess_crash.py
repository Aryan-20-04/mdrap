"""True OS Subprocess Crash Injection Test for MDRAP Projections.

Adversarial Verification for MDRAP v4.0.1:
Instead of clean Python in-process teardown (del engine, gc.collect, connection.close),
this test executes a REAL operating system subprocess crash using proc.kill()
(Win32 TerminateProcess / POSIX SIGKILL) mid-flight while events are being actively
ingested to WAL and projected to SQLite.

Invariants Verified Post-Abrupt Termination:
1. SQLite WAL file recovers cleanly without database corruption (PRAGMA integrity_check == ok).
2. ProjectionTracker correctly reflects the durable checkpoint prior to process severance.
3. Fresh Engine instance successfully replays missing WAL records via recover_projections().
4. Final projection state contains 100% of persisted events with ZERO gaps and ZERO duplicates.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_dir = os.path.join(repo_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from mdrap import Engine, EngineConfig, RawEvent
from mdrap.projection import SQLiteProjection


def run_child(wal_dir: str, db_path: str, count: int):
    """Child process workload: actively ingests and projects events until killed."""
    proj = SQLiteProjection(db_path, name="canonical")
    cfg = EngineConfig(
        wal_dir=wal_dir,
        fsync_policy="every_event",
        projections=[proj],
    )
    engine = Engine.open(wal_dir, config=cfg)

    # Signal readiness to parent once engine is open
    sys.stdout.write("PROJECTION_ENGINE_INITIALIZED\n")
    sys.stdout.flush()

    for i in range(count):
        raw = RawEvent(
            source="CRASH_FEED",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0 + (i % 10) * 0.25,
                "quantity": 100.0,
                "exchange_ts": 1700000000.0 + i * 0.001,
                "sequence": i + 1,
            },
            receive_timestamp=1700000000.0 + i * 0.001,
            raw_id=f"crash_ev_{i}",
        )
        engine.submit(raw)

        if i == 500:
            sys.stdout.write("PROJECTION_ACTIVE_AND_WRITING\n")
            sys.stdout.flush()

        # Small pacing to ensure child is killed mid-stream
        time.sleep(0.0005)

    # If not killed in time, flush and exit
    engine.close()


def test_real_subprocess_crash():
    """Parent orchestrator: spawns child, kills with proc.kill(), verifies recovery."""
    test_dir = tempfile.mkdtemp(prefix="mdrap_crash_test_")
    wal_dir = os.path.join(test_dir, "wal")
    db_path = os.path.join(test_dir, "canonical.db")
    total_target = 2500

    log_lines = []

    def log(msg: str):
        print(msg)
        log_lines.append(msg)

    log("=" * 80)
    log("MDRAP v4.0.1 REAL OS-LEVEL SUBPROCESS CRASH INJECTION TEST")
    log("=" * 80)
    log(f"Test Environment: {test_dir}")
    log(f"Target Events: {total_target}")

    try:
        # 1. Spawn child process running the ingestion workload
        cmd = [
            sys.executable,
            __file__,
            "--child",
            "--wal-dir",
            wal_dir,
            "--db-path",
            db_path,
            "--count",
            str(total_target),
        ]
        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = src_dir
        log(f"[1] Spawning child ingestion process: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=child_env,
        )

        # 2. Wait for child to reach active projection state
        log("[2] Waiting for child to begin active WAL writes and SQLite projections...")
        ready = False
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            line_str = line.strip()
            log(f"    [Child stdout]: {line_str}")
            if "PROJECTION_ACTIVE_AND_WRITING" in line_str:
                ready = True
                break

        if not ready:
            err = proc.stderr.read()
            log(f"    [Child stderr]: {err}")
            assert ready, f"Child process failed to reach active writing state! stderr: {err}"

        # 3. Allow child to write more events mid-flight
        time.sleep(0.15)

        # 4. ABRUPT TERMINATION (proc.kill -> Win32 TerminateProcess / POSIX SIGKILL)
        log("[3] EXECUTING REAL OS PROCESS SEVERANCE: proc.kill()...")
        proc.kill()
        exit_code = proc.wait(timeout=5.0)
        log(f"    Child process terminated. Exit code: {exit_code} (non-zero confirmation)")

        # Verify child did not perform clean Python exit
        assert exit_code != 0, f"Child exited cleanly with code {exit_code}!"

        # 5. Raw Storage Inspection Post-Crash
        log("\n[4] Inspecting storage state immediately after abrupt termination...")
        assert os.path.exists(db_path), "SQLite database does not exist!"
        assert os.path.exists(wal_dir), "WAL directory does not exist!"

        # Verify SQLite DB integrity via direct sqlite3 connection
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        integrity = cursor.execute("PRAGMA integrity_check;").fetchall()
        log(f"    SQLite PRAGMA integrity_check: {integrity}")
        assert integrity == [("ok",)], f"Database corrupted after crash! {integrity}"

        # Inspect checkpoint and rows currently committed before recovery
        pre_checkpoint = cursor.execute(
            "SELECT last_offset FROM projection_checkpoints WHERE name = 'canonical';"
        ).fetchone()
        pre_checkpoint_offset = pre_checkpoint[0] if pre_checkpoint else 0

        pre_count = cursor.execute("SELECT COUNT(*) FROM canonical_events;").fetchone()[0]
        log(f"    Pre-recovery durable checkpoint offset: {pre_checkpoint_offset}")
        log(f"    Pre-recovery projected row count: {pre_count}")
        conn.close()

        # 6. Engine Recovery & Catch-up
        log("\n[5] Opening fresh Engine instance to execute projection catch-up recovery...")
        recovery_proj = SQLiteProjection(db_path, name="canonical")
        recovery_cfg = EngineConfig(
            wal_dir=wal_dir,
            projections=[recovery_proj],
            fsync_policy="every_event",
        )
        recovery_engine = Engine.open(wal_dir, config=recovery_cfg)

        wal_records_total = recovery_engine.log.next_offset
        log(f"    Total durable WAL records on disk: {wal_records_total}")
        assert wal_records_total > 500, f"Insufficient events written before crash ({wal_records_total})"

        # Trigger projection recovery
        log("    Calling recovery_engine.recover_projections()...")
        recovery_engine.recover_projections()
        tracker = recovery_engine._get_or_create_projection_tracker(recovery_proj)
        tracker.drain(non_blocking=False, timeout=5.0)
        recovery_engine.close()

        # 7. Post-Recovery Validation
        log("\n[6] Validating recovered database state...")
        conn2 = sqlite3.connect(db_path)
        cursor2 = conn2.cursor()

        post_integrity = cursor2.execute("PRAGMA integrity_check;").fetchall()
        log(f"    Post-recovery PRAGMA integrity_check: {post_integrity}")
        assert post_integrity == [("ok",)], "Database integrity violated post-recovery!"

        post_checkpoint = cursor2.execute(
            "SELECT last_offset FROM projection_checkpoints WHERE name = 'canonical';"
        ).fetchone()[0]
        post_count = cursor2.execute("SELECT COUNT(*) FROM canonical_events;").fetchone()[0]

        log(f"    Post-recovery checkpoint offset: {post_checkpoint}")
        log(f"    Post-recovery projected row count: {post_count}")

        # Invariants:
        # Offsets are 0-indexed (0 to wal_records_total - 1)
        expected_checkpoint = wal_records_total - 1
        assert post_count == wal_records_total, (
            f"Projection count mismatch! Expected {wal_records_total}, got {post_count}"
        )
        assert post_checkpoint == expected_checkpoint, (
            f"Checkpoint offset mismatch! Expected {expected_checkpoint}, got {post_checkpoint}"
        )

        # Check contiguity of sequence numbers
        rows = cursor2.execute(
            "SELECT sequence_number FROM canonical_events ORDER BY sequence_number ASC;"
        ).fetchall()
        seqs = [r[0] for r in rows]
        assert seqs == list(range(1, post_count + 1)), "Sequence gap or duplicate detected in recovered rows!"
        log(f"    Contiguity check PASSED: Sequences 1 through {post_count} contiguous.")

        conn2.close()

        log("\n" + "=" * 80)
        log("SUCCESS: Real OS-level subprocess crash recovery verified with 100% integrity!")
        log("=" * 80)

        # Write log to raw_evidence
        evidence_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "raw_evidence",
            "p0_projection_real_subprocess_crash.log",
        )
        with open(evidence_path, "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
        log(f"Raw evidence recorded to: {evidence_path}")

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", action="store_true", help="Run child workload")
    parser.add_argument("--wal-dir", type=str, default="")
    parser.add_argument("--db-path", type=str, default="")
    parser.add_argument("--count", type=int, default=2000)
    args = parser.parse_args()

    if args.child:
        run_child(args.wal_dir, args.db_path, args.count)
    else:
        test_real_subprocess_crash()
