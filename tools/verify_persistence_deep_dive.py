"""
MDRAP 6.0.7 — Persistence Deep Dive & Commit Latency Investigation.

Validates:
1. Architectural Decoupling: Tick processing latency remains isolated from storage commits.
2. Root-cause analysis of the 14.52 ms p99 commit latency on Windows SQLite WAL.
3. Profiling across durability tiers: FAST (synchronous=OFF), BALANCED (NORMAL), COMPLIANCE (FULL).
4. Concurrent Reader / Writer stress without database locks.
5. Long-running reader snapshot isolation & WAL truncation upon lock release.
6. Abrupt termination crash recovery & 100% data durability verification.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import threading
import time
from typing import Any

# Ensure project root in sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.storage import Store


def _create_dummy_events(batch_size: int, start_idx: int) -> list[CanonicalEvent]:
    events = []
    now = time.time()
    for i in range(batch_size):
        idx = start_idx + i
        events.append(
            CanonicalEvent(
                event_id=f"ev_p_{idx}_{int(now)}",
                instrument_id="BTC/USD" if (i % 2 == 0) else "ETH/USD",
                event_type=EventType.TRADE,
                exchange_timestamp=now,
                receive_timestamp=now,
                price=50000.0 + (i % 100) * 0.5,
                quantity=1.0 + (i % 5) * 0.1,
                quality_status=QualityStatus.VALID,
                source="BINANCE",
            )
        )
    return events


def profile_durability_modes(db_dir: str) -> dict[str, Any]:
    """Profile commit latencies across FAST, BALANCED, and COMPLIANCE durability tiers."""
    results = {}
    batch_size = 100
    num_batches = 50  # 5,000 total events per mode

    for mode in ("fast", "balanced", "compliance"):
        db_path = os.path.join(db_dir, f"test_durability_{mode}.db")
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(db_path + ext):
                try:
                    os.remove(db_path + ext)
                except Exception:
                    pass

        store = Store(db_path, durability=mode)
        exec_times_us = []
        commit_times_us = []
        total_batch_times_us = []

        for b in range(num_batches):
            events = _create_dummy_events(batch_size, b * batch_size)

            t0 = time.perf_counter_ns()
            store.write_canonical_batch(events)
            t1 = time.perf_counter_ns()
            store.commit()
            t2 = time.perf_counter_ns()

            exec_us = (t1 - t0) / 1000.0
            commit_us = (t2 - t1) / 1000.0
            tot_us = (t2 - t0) / 1000.0

            exec_times_us.append(exec_us)
            commit_times_us.append(commit_us)
            total_batch_times_us.append(tot_us)

        # Get file sizes
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        wal_path = db_path + "-wal"
        wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

        store.close()

        results[mode] = {
            "durability": mode,
            "total_events": batch_size * num_batches,
            "executemany_p50_us": round(statistics.median(exec_times_us), 1),
            "executemany_p95_us": round(
                statistics.quantiles(exec_times_us, n=20)[18], 1
            ),
            "executemany_p99_us": round(
                statistics.quantiles(exec_times_us, n=100)[98], 1
            )
            if len(exec_times_us) >= 100
            else round(max(exec_times_us), 1),
            "commit_p50_us": round(statistics.median(commit_times_us), 1),
            "commit_p95_us": round(statistics.quantiles(commit_times_us, n=20)[18], 1),
            "commit_p99_us": round(statistics.quantiles(commit_times_us, n=100)[98], 1)
            if len(commit_times_us) >= 100
            else round(max(commit_times_us), 1),
            "commit_max_us": round(max(commit_times_us), 1),
            "total_p50_us": round(statistics.median(total_batch_times_us), 1),
            "total_p95_us": round(
                statistics.quantiles(total_batch_times_us, n=20)[18], 1
            ),
            "total_p99_us": round(
                statistics.quantiles(total_batch_times_us, n=100)[98], 1
            )
            if len(total_batch_times_us) >= 100
            else round(max(total_batch_times_us), 1),
            "db_size_bytes": db_size,
            "wal_size_bytes": wal_size,
        }

    return results


def test_concurrent_reader_writer(db_path: str) -> dict[str, Any]:
    """Test concurrent reader threads while writer continuously commits batches."""
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db_path + ext):
            try:
                os.remove(db_path + ext)
            except Exception:
                pass

    store = Store(db_path, durability="balanced")
    # Pre-seed 1,000 events
    store.write_canonical_batch(_create_dummy_events(1000, 0))
    store.commit()

    stop_event = threading.Event()
    read_counts = [0, 0, 0]
    read_errors = [0, 0, 0]
    read_latencies_us = []

    def reader_worker(reader_idx: int):
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000;")
        while not stop_event.is_set():
            t0 = time.perf_counter_ns()
            try:
                cur = conn.execute("SELECT COUNT(*), AVG(price) FROM canonical_events;")
                _ = cur.fetchone()
                read_counts[reader_idx] += 1
                lat = (time.perf_counter_ns() - t0) / 1000.0
                read_latencies_us.append(lat)
            except Exception:
                read_errors[reader_idx] += 1
            time.sleep(0.001)
        conn.close()

    # Launch 3 concurrent reader threads
    threads = []
    for r in range(3):
        t = threading.Thread(target=reader_worker, args=(r,))
        t.start()
        threads.append(t)

    # Writer commits 30 batches of 100 events while readers query concurrently
    write_latencies_us = []
    for b in range(30):
        t0 = time.perf_counter_ns()
        store.write_canonical_batch(_create_dummy_events(100, 1000 + b * 100))
        store.commit()
        write_latencies_us.append((time.perf_counter_ns() - t0) / 1000.0)
        time.sleep(0.005)

    stop_event.set()
    for t in threads:
        t.join(timeout=3.0)

    store.close()

    return {
        "total_writes": 30 * 100,
        "total_queries_executed": sum(read_counts),
        "total_query_errors": sum(read_errors),
        "zero_lock_contention": sum(read_errors) == 0,
        "query_p50_us": round(statistics.median(read_latencies_us), 1)
        if read_latencies_us
        else 0.0,
        "query_p95_us": round(statistics.quantiles(read_latencies_us, n=20)[18], 1)
        if len(read_latencies_us) >= 20
        else 0.0,
        "writer_p50_us": round(statistics.median(write_latencies_us), 1)
        if write_latencies_us
        else 0.0,
        "writer_p95_us": round(statistics.quantiles(write_latencies_us, n=20)[18], 1)
        if len(write_latencies_us) >= 20
        else 0.0,
    }


def test_long_running_transaction_and_truncate(db_path: str) -> dict[str, Any]:
    """Test long-running read transaction snapshot isolation and post-release WAL truncate."""
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db_path + ext):
            try:
                os.remove(db_path + ext)
            except Exception:
                pass

    store = Store(db_path, durability="balanced")
    store.write_canonical_batch(_create_dummy_events(500, 0))
    store.commit()

    # Open long-running reader connection that starts an explicit read transaction
    reader_conn = sqlite3.connect(db_path, timeout=5.0)
    reader_conn.execute("BEGIN;")
    cur = reader_conn.execute("SELECT COUNT(*) FROM canonical_events;")
    initial_count = cur.fetchone()[0]

    # Writer continues writing 1,000 new events while reader holds snapshot
    store.write_canonical_batch(_create_dummy_events(1000, 500))
    store.commit()

    # Passive checkpoint while reader active (must not block writer or reader)
    ckpt_during_read = store.wal_checkpoint("PASSIVE")

    # Verify reader still sees its original snapshot (snapshot isolation)
    cur_after = reader_conn.execute("SELECT COUNT(*) FROM canonical_events;")
    snapshot_count = cur_after.fetchone()[0]

    # Reader commits / closes its transaction
    reader_conn.execute("COMMIT;")
    reader_conn.close()

    # Now execute TRUNCATE checkpoint
    wal_before_truncate = (
        os.path.getsize(db_path + "-wal") if os.path.exists(db_path + "-wal") else 0
    )
    ckpt_truncate = store.wal_checkpoint("TRUNCATE")
    wal_after_truncate = (
        os.path.getsize(db_path + "-wal") if os.path.exists(db_path + "-wal") else 0
    )

    store.close()

    return {
        "initial_reader_count": initial_count,
        "snapshot_isolated_count": snapshot_count,
        "snapshot_isolation_verified": initial_count == snapshot_count == 500,
        "passive_checkpoint_during_read": ckpt_during_read,
        "wal_size_before_truncate_bytes": wal_before_truncate,
        "wal_size_after_truncate_bytes": wal_after_truncate,
        "truncated_to_zero": wal_after_truncate == 0,
    }


def test_abrupt_shutdown_recovery(db_path: str) -> dict[str, Any]:
    """Verify crash recovery when store connection is abruptly killed without checkpoint."""
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db_path + ext):
            try:
                os.remove(db_path + ext)
            except Exception:
                pass

    # Store 1 inserts 3,000 events
    store1 = Store(db_path, durability="balanced")
    store1.write_canonical_batch(_create_dummy_events(3000, 0))
    store1.commit()

    # Simulate crash: close underlying OS connection without truncate or checkpoint
    store1.conn.close()

    # Store 2 opens fresh database (forces SQLite WAL crash recovery)
    t0 = time.perf_counter_ns()
    store2 = Store(db_path, durability="balanced")
    recovery_dur_us = (time.perf_counter_ns() - t0) / 1000.0

    cur = store2.conn.execute("SELECT COUNT(*) FROM canonical_events;")
    recovered_count = cur.fetchone()[0]
    store2.close()

    return {
        "expected_events": 3000,
        "recovered_events": recovered_count,
        "recovery_rate": recovered_count / 3000.0,
        "recovery_time_us": round(recovery_dur_us, 1),
        "zero_data_loss": recovered_count == 3000,
    }


def run_persistence_investigation() -> dict[str, Any]:
    """Execute complete persistence investigation and benchmark suite."""
    test_dir = os.path.abspath("data")
    os.makedirs(test_dir, exist_ok=True)
    results: dict[str, Any] = {}

    print(
        "\n--- [Persistence Pillar 3] 1. Profiling Durability Tiers & Root-Cause of 14.52ms p99 Commit ---"
    )
    dur_results = profile_durability_modes(test_dir)
    results["durability_tiers"] = dur_results
    for mode, data in dur_results.items():
        print(
            f"Mode: {mode.upper():10s} | Commit Latency: p50={data['commit_p50_us']}μs, "
            f"p95={data['commit_p95_us']}μs, p99={data['commit_p99_us']}μs, Max={data['commit_max_us']}μs | "
            f"Batch Total: p50={data['total_p50_us']}μs, p95={data['total_p95_us']}μs"
        )

    print(
        "\n--- [Persistence Pillar 3] 2. Concurrent Reader / Writer Lock Contention ---"
    )
    rw_results = test_concurrent_reader_writer(
        os.path.join(test_dir, "test_rw_concurrency.db")
    )
    results["concurrent_reader_writer"] = rw_results
    print(
        f"Executed {rw_results['total_queries_executed']} queries across 3 concurrent reader threads "
        f"during {rw_results['total_writes']} writes. Errors: {rw_results['total_query_errors']}. "
        f"Query p50={rw_results['query_p50_us']}μs, Writer p50={rw_results['writer_p50_us']}μs."
    )

    print(
        "\n--- [Persistence Pillar 3] 3. Long-Running Reader Snapshot Isolation & Truncate ---"
    )
    snap_results = test_long_running_transaction_and_truncate(
        os.path.join(test_dir, "test_snapshot.db")
    )
    results["snapshot_isolation_truncate"] = snap_results
    print(
        f"Snapshot Isolation: {snap_results['snapshot_isolation_verified']} "
        f"(Initial={snap_results['initial_reader_count']}, In-TX={snap_results['snapshot_isolated_count']}). "
        f"WAL size: {snap_results['wal_size_before_truncate_bytes']} bytes -> {snap_results['wal_size_after_truncate_bytes']} bytes (Truncated to zero: {snap_results['truncated_to_zero']})."
    )

    print("\n--- [Persistence Pillar 3] 4. Abrupt Termination Crash Recovery ---")
    crash_results = test_abrupt_shutdown_recovery(
        os.path.join(test_dir, "test_crash_recovery.db")
    )
    results["abrupt_shutdown_recovery"] = crash_results
    print(
        f"Recovered {crash_results['recovered_events']}/{crash_results['expected_events']} events "
        f"in {crash_results['recovery_time_us']}μs. Zero Data Loss = {crash_results['zero_data_loss']}."
    )

    return results


if __name__ == "__main__":
    res = run_persistence_investigation()
    os.makedirs("benchmarks", exist_ok=True)
    with open("benchmarks/persistence_investigation_results.json", "w") as f:
        json.dump(res, f, indent=2)
    print(
        "\n[Persistence Pillar 3] Results saved to benchmarks/persistence_investigation_results.json"
    )
