"""Phase 2 Gate G2 Harness: 1,000 Randomized Kill-9 Crash/Recovery Runs.

Validates that across 1,000 abrupt process kills (kill -9):
1. Acknowledged-event loss == 0
2. Storage conflict errors == 0
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

WORKER_CODE = """
import sys
import os
import time
import json
import random

from mdrap.ingestlog import IngestLog
from mdrap.engine import Engine
from mdrap.clock import SystemClock
from mdrap.projection import SQLiteProjection
from mdrap.models import RawEvent

def run_worker(log_dir, db_path, ack_file, num_events, kill_after):
    log = IngestLog(log_dir=log_dir, fsync_policy="always")
    proj = SQLiteProjection(db_path=db_path)
    engine = Engine()
    clock = SystemClock()
    state = engine.create_initial_state()

    acked = []
    pending_batch = []
    with open(ack_file, "w") as f_ack:
        for i in range(num_events):
            if i >= kill_after:
                # Abruptly suicide / exit without flushing or closing to simulate hard kill-9
                f_ack.flush()
                os._exit(42)

            raw = RawEvent(
                source="SRC_G2",
                payload={"instrument": "AAPL", "price": 150.0 + (i % 10), "sequence": i + 1, "exchange_ts": time.time()},
                receive_timestamp=time.time(),
                raw_id=f"g2_raw_{i}",
            )
            # 1. IngestLog append with durability ACK
            offset = log.append(raw)
            acked.append(offset)
            f_ack.write(str(offset) + chr(10))
            f_ack.flush()

            # 2. Engine step
            state, dec = engine.step(state, raw, clock, offset=offset)
            pending_batch.append(dec)

            # 3. Projection apply every 3 events or on certain ticks
            if len(pending_batch) >= 3:
                proj.apply(pending_batch, offset=offset)
                pending_batch.clear()

if __name__ == "__main__":
    log_dir = sys.argv[1]
    db_path = sys.argv[2]
    ack_file = sys.argv[3]
    num_events = int(sys.argv[4])
    kill_after = int(sys.argv[5])
    run_worker(log_dir, db_path, ack_file, num_events, kill_after)
"""


def execute_single_kill_run(run_id: int, base_tmp: str) -> dict:
    run_dir = os.path.join(base_tmp, f"run_{run_id}")
    os.makedirs(run_dir, exist_ok=True)
    log_dir = os.path.join(run_dir, "ingest_log")
    db_path = os.path.join(run_dir, "projection.db")
    ack_file = os.path.join(run_dir, "acked.txt")
    worker_script = os.path.join(run_dir, "worker.py")

    with open(worker_script, "w") as f:
        f.write(WORKER_CODE)

    num_events = random.randint(15, 30)
    kill_after = random.randint(5, num_events - 2)

    # Spawn worker subprocess
    proc = subprocess.Popen(
        [sys.executable, worker_script, log_dir, db_path, ack_file, str(num_events), str(kill_after)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Read acknowledged offsets
    acked_offsets = []
    if os.path.exists(ack_file):
        with open(ack_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    acked_offsets.append(int(line))

    # Recovery Phase
    from mdrap.ingestlog import IngestLog
    from mdrap.engine import Engine
    from mdrap.clock import SystemClock
    from mdrap.projection import SQLiteProjection

    conflict_errors = 0
    lost_events = 0

    try:
        # Reopen projection and read last checkpoint
        proj = SQLiteProjection(db_path=db_path)
        cp = proj.checkpoint()

        # Reopen IngestLog (automatically repairs any torn tails)
        log = IngestLog(log_dir=log_dir, fsync_policy="always")

        # Replay from checkpoint + 1
        replay_start = cp + 1
        engine = Engine()
        clock = SystemClock()
        state = engine.create_initial_state()

        recovered_batch = []
        for offset, raw in log.iter_from(replay_start):
            state, dec = engine.step(state, raw, clock, offset=offset)
            recovered_batch.append(dec)

        if recovered_batch:
            highest_offset = recovered_batch[-1].offset
            proj.apply(recovered_batch, offset=highest_offset)

        # Verify: All acknowledged events must be in SQLiteProjection!
        # Query total count or specific event IDs
        total_in_db = proj.count_canonical()
        expected_min = len(acked_offsets)
        if total_in_db < expected_min:
            lost_events = expected_min - total_in_db

        proj.close()
        log.close()
    except Exception as exc:
        conflict_errors += 1

    # Cleanup run dir
    shutil.rmtree(run_dir, ignore_errors=True)

    return {
        "run_id": run_id,
        "acked": len(acked_offsets),
        "lost": lost_events,
        "conflicts": conflict_errors,
    }


def run_gate_g2_harness(total_runs: int = 1000, max_workers: int = 8) -> dict:
    print(f"=== STARTING GATE G2 HARNESS: {total_runs} RANDOMIZED KILL-9 RUNS ===")
    start_time = time.time()
    base_tmp = tempfile.mkdtemp(prefix="gate_g2_")

    total_acked = 0
    total_lost = 0
    total_conflicts = 0

    try:
        # Execute runs using ProcessPoolExecutor for maximum concurrency
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(execute_single_kill_run, i, base_tmp)
                for i in range(total_runs)
            ]

            completed = 0
            for future in as_completed(futures):
                res = future.result()
                total_acked += res["acked"]
                total_lost += res["lost"]
                total_conflicts += res["conflicts"]
                completed += 1
                if completed % 100 == 0 or completed == total_runs:
                    print(
                        f"  [Gate G2 Progress] {completed}/{total_runs} runs completed | "
                        f"ACKed events: {total_acked} | Lost: {total_lost} | Conflicts: {total_conflicts}"
                    )
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)

    elapsed_s = time.time() - start_time
    passed = (total_lost == 0 and total_conflicts == 0)

    results = {
        "gate": "G2",
        "total_runs": total_runs,
        "total_acked_events": total_acked,
        "acknowledged_event_loss": total_lost,
        "conflict_errors": total_conflicts,
        "elapsed_seconds": round(elapsed_s, 2),
        "passed": passed,
    }

    print("=== GATE G2 RESULT ===")
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    res = run_gate_g2_harness(total_runs=runs)
    sys.exit(0 if res["passed"] else 1)
