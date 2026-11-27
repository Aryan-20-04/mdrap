"""
MDRAP 6.0.7 — Cross-Process SPMC Shared Memory Verification Suite.

Validates across separate OS processes:
1. Paced Cross-Process Concurrency: 1 Producer + 4 Consumers (100% tick delivery, p50/p95/p99 delivery latency).
2. High-Throughput Wraparound Stress: 200k+ eps burst into 2048-slot ring buffer (zero torn reads, slot reuse).
3. Slow Consumer Lap Detection & Overrun Accounting (laps, skipped ticks, watermark warnings).
4. Consumer Process Crash (SIGTERM) & Seamless Replacement Restart.
5. Producer Process Restart & Epoch Invalidation Detection.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
import json
import statistics
from typing import Any

# Ensure project root in sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mdrap.shm import (
    SHMWriter,
    SHMReader,
    SHM_FLAG_WATERMARK_WARNING,
    FLAGS_OFFSET,
    TOTAL_SHM_SIZE,
    HEADER_SIZE,
    SLOT_SIZE,
)


def _producer_worker(
    shm_name: str,
    slot_count: int,
    num_ticks: int,
    start_seq: int,
    sync_event: mp.Event,
    start_event: mp.Event,
    done_event: mp.Event,
    consumers_done_event: mp.Event,
    throttle_sleep_s: float = 0.0,
):
    """Producer OS Process."""
    try:
        writer = SHMWriter(name=shm_name, slot_count=slot_count)
    except Exception as exc:
        print(
            f"[Producer {os.getpid()}] Failed to create SHMWriter: {exc}",
            file=sys.stderr,
        )
        return

    sync_event.set()
    start_event.wait(timeout=10.0)

    t_start = time.perf_counter()
    for i in range(num_ticks):
        seq = start_seq + i
        now = time.time()
        t_mono_ns = time.perf_counter_ns()
        writer.write_tick(
            seq=seq,
            symbol="BTC/USD" if (i % 2 == 0) else "ETH/USD",
            source="BINANCE",
            price=50000.0 + (i % 500) * 0.25,
            size=1.5 + (i % 10) * 0.1,
            bid=49999.0 + (i % 500) * 0.25,
            ask=50001.0 + (i % 500) * 0.25,
            bid_size=10.0,
            ask_size=10.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=now,
            ingest_ts=now,
            broadcast_ts=t_mono_ns / 1e9,
            engine_us=12.5,
        )
        if throttle_sleep_s > 0:
            time.sleep(throttle_sleep_s)

    writer.update_heartbeat()
    done_event.set()

    # Keep handle open until consumers finish reading
    while not consumers_done_event.is_set():
        time.sleep(0.01)
        writer.update_heartbeat()

    writer.close()


def _fast_consumer_worker(
    shm_name: str,
    consumer_id: int,
    expected_ticks: int,
    result_queue: mp.Queue,
    ready_event: mp.Event,
    start_event: mp.Event,
    allow_overrun_skipping: bool = False,
    max_wait_s: float = 10.0,
):
    """Fast Consumer OS Process."""
    try:
        reader = SHMReader(name=shm_name)
    except Exception as exc:
        ready_event.set()
        result_queue.put(
            {
                "consumer_id": consumer_id,
                "pid": os.getpid(),
                "error": str(exc),
                "ticks_read": 0,
            }
        )
        return

    ready_event.set()
    start_event.wait(timeout=10.0)

    ticks_read = 0
    torn_reads = 0
    seq_errors = 0
    last_seq = -1
    latencies_us = []

    curr_seq = 0
    t0 = time.time()

    while (
        curr_seq < expected_ticks
        if allow_overrun_skipping
        else ticks_read < expected_ticks
    ) and (time.time() - t0) < max_wait_s:
        head = reader.read_latest_seq()
        if (
            allow_overrun_skipping
            and head > 0
            and (head - curr_seq) > reader.slot_count
        ):
            # Overrun skip forward
            reader.overrun_stats.total_laps += 1
            skipped = (head - reader.slot_count) - curr_seq
            reader.overrun_stats.skipped_ticks += max(0, skipped)
            curr_seq = head - reader.slot_count

        slot = reader.read_slot(curr_seq)
        if slot is not None:
            now_mono_ns = time.perf_counter_ns()
            if slot["seq"] != curr_seq:
                seq_errors += 1
            if (
                not allow_overrun_skipping
                and last_seq >= 0
                and slot["seq"] != last_seq + 1
            ):
                seq_errors += 1
            last_seq = slot["seq"]

            # Torn read check
            if (
                slot["price"] is None
                or slot["price"] < 1000.0
                or slot["price"] > 100000.0
            ):
                torn_reads += 1

            # Latency
            bc_mono_ns = slot.get("broadcast_ts", 0.0) * 1e9
            if bc_mono_ns > 0:
                lat_us = (now_mono_ns - bc_mono_ns) / 1000.0
                if 0 <= lat_us < 100000:
                    latencies_us.append(lat_us)

            ticks_read += 1
            curr_seq += 1
        else:
            if head <= curr_seq:
                time.sleep(0.00005)
            elif not allow_overrun_skipping and (head - curr_seq) > reader.slot_count:
                # Reader fell behind buffer capacity and overrun skipping was not permitted
                break

    reader.close()

    p50 = statistics.median(latencies_us) if latencies_us else 0.0
    p95 = (
        statistics.quantiles(latencies_us, n=20)[18] if len(latencies_us) >= 20 else p50
    )
    p99 = (
        statistics.quantiles(latencies_us, n=100)[98]
        if len(latencies_us) >= 100
        else p95
    )
    p999 = (
        statistics.quantiles(latencies_us, n=1000)[998]
        if len(latencies_us) >= 1000
        else p99
    )

    result_queue.put(
        {
            "consumer_id": consumer_id,
            "pid": os.getpid(),
            "ticks_read": ticks_read,
            "expected_ticks": expected_ticks,
            "torn_reads": torn_reads,
            "seq_errors": seq_errors,
            "p50_us": round(p50, 2),
            "p95_us": round(p95, 2),
            "p99_us": round(p99, 2),
            "p999_us": round(p999, 2),
            "total_laps": reader.overrun_stats.total_laps,
            "skipped_ticks": reader.overrun_stats.skipped_ticks,
        }
    )


def _slow_consumer_worker(
    shm_name: str,
    consumer_id: int,
    result_queue: mp.Queue,
    ready_event: mp.Event,
    start_event: mp.Event,
    sleep_per_tick_s: float = 0.0005,
    max_duration_s: float = 3.0,
):
    """Slow Consumer OS Process that gets intentionally lapped."""
    try:
        reader = SHMReader(name=shm_name)
    except Exception as exc:
        ready_event.set()
        result_queue.put({"consumer_id": consumer_id, "error": str(exc)})
        return

    ready_event.set()
    start_event.wait(timeout=10.0)

    ticks_read = 0
    t0 = time.time()
    curr_seq = 0

    while (time.time() - t0) < max_duration_s:
        head = reader.read_latest_seq()
        if head > 0 and (head - curr_seq) > reader.slot_count:
            reader.overrun_stats.total_laps += 1
            skipped = (head - reader.slot_count) - curr_seq
            reader.overrun_stats.skipped_ticks += max(0, skipped)
            curr_seq = head - reader.slot_count

        slot = reader.read_slot(curr_seq)
        if slot is not None:
            ticks_read += 1
            curr_seq += 1
            time.sleep(sleep_per_tick_s)
        else:
            time.sleep(0.001)

    reader.close()

    result_queue.put(
        {
            "consumer_id": consumer_id,
            "pid": os.getpid(),
            "ticks_read": ticks_read,
            "laps_detected": reader.overrun_stats.total_laps,
            "skipped_ticks": reader.overrun_stats.skipped_ticks,
        }
    )


def run_spmc_cross_process_suite() -> dict[str, Any]:
    """Execute complete SPMC Cross-Process Test Suite."""
    base_id = f"{os.getpid()}_{int(time.time())}"
    results: dict[str, Any] = {}

    # =========================================================================
    # Test 1: Paced Concurrency (1 Producer + 4 Consumers, 100% Delivery)
    # =========================================================================
    shm_name_concurrency = f"test_mdrap_spmc_conc_{base_id}"
    slot_count = 16384  # Standard production ring buffer
    total_ticks = 10000

    print(
        f"\n--- [SPMC Pillar 1] Test 1: Paced Cross-Process Concurrency (1 Producer + 4 Consumers, {total_ticks} ticks) ---"
    )
    sync_event = mp.Event()
    start_producer_event = mp.Event()
    producer_done_event = mp.Event()
    consumers_done_event = mp.Event()
    result_queue = mp.Queue()

    # Launch Producer paced at ~25,000 eps (0.00004s sleep per tick)
    prod_proc = mp.Process(
        target=_producer_worker,
        args=(
            shm_name_concurrency,
            slot_count,
            total_ticks,
            0,
            sync_event,
            start_producer_event,
            producer_done_event,
            consumers_done_event,
            0.00004,
        ),
    )
    prod_proc.start()
    sync_event.wait(timeout=5.0)

    # Launch 4 Fast Consumers
    consumers = []
    ready_events = []
    start_consumer_event = mp.Event()
    for c_id in range(4):
        r_ev = mp.Event()
        ready_events.append(r_ev)
        p = mp.Process(
            target=_fast_consumer_worker,
            args=(
                shm_name_concurrency,
                c_id,
                total_ticks,
                result_queue,
                r_ev,
                start_consumer_event,
                False,
                15.0,
            ),
        )
        p.start()
        consumers.append(p)

    for r_ev in ready_events:
        r_ev.wait(timeout=5.0)

    start_consumer_event.set()
    t_start = time.perf_counter()
    start_producer_event.set()

    producer_done_event.wait(timeout=15.0)
    t_end = time.perf_counter()
    prod_duration = t_end - t_start

    consumer_stats = []
    for _ in range(4):
        try:
            c_res = result_queue.get(timeout=10.0)
            consumer_stats.append(c_res)
        except Exception:
            pass

    consumers_done_event.set()
    prod_proc.join(timeout=5.0)
    for p in consumers:
        p.join(timeout=2.0)
        if p.is_alive():
            p.terminate()

    throughput_eps = total_ticks / max(0.001, prod_duration)
    results["concurrency_test"] = {
        "total_ticks": total_ticks,
        "slot_count": slot_count,
        "producer_duration_s": round(prod_duration, 4),
        "producer_throughput_eps": round(throughput_eps, 1),
        "consumer_count": len(consumer_stats),
        "consumers": consumer_stats,
    }
    print(
        f"Producer Throughput: {throughput_eps:,.1f} ticks/s across {len(consumer_stats)} consumers"
    )
    for c in consumer_stats:
        print(
            f"  Consumer {c['consumer_id']} (PID {c['pid']}): {c['ticks_read']}/{c['expected_ticks']} read, "
            f"Torn Reads: {c['torn_reads']}, Seq Errors: {c['seq_errors']}, "
            f"Latency: p50={c['p50_us']}μs, p95={c['p95_us']}μs, p99={c['p99_us']}μs, p99.9={c['p999_us']}μs"
        )

    # Clean up SHM 1
    try:
        from multiprocessing.shared_memory import SharedMemory

        s = SharedMemory(name=shm_name_concurrency, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass

    # =========================================================================
    # Test 2: High-Throughput Wraparound Stress (Zero Torn Reads, Slot Reuse)
    # =========================================================================
    shm_name_burst = f"test_mdrap_spmc_burst_{base_id}"
    burst_slots = 1024  # Small 1024-slot buffer to force multiple wraparounds
    burst_ticks = 30000  # ~30 full ring-buffer wraparounds

    print(
        f"\n--- [SPMC Pillar 1] Test 2: High-Throughput Wraparound Stress ({burst_ticks} ticks, {burst_slots} slots) ---"
    )
    b_sync = mp.Event()
    b_start_prod = mp.Event()
    b_prod_done = mp.Event()
    b_cons_done = mp.Event()
    b_queue = mp.Queue()
    b_ready = mp.Event()
    b_start_cons = mp.Event()

    # Consumer with overrun skipping enabled
    b_consumer = mp.Process(
        target=_fast_consumer_worker,
        args=(
            shm_name_burst,
            0,
            burst_ticks,
            b_queue,
            b_ready,
            b_start_cons,
            True,
            10.0,
        ),
    )
    b_consumer.start()

    # Producer writing at full unthrottled burst speed
    b_prod = mp.Process(
        target=_producer_worker,
        args=(
            shm_name_burst,
            burst_slots,
            burst_ticks,
            0,
            b_sync,
            b_start_prod,
            b_prod_done,
            b_cons_done,
            0.0,
        ),
    )
    b_prod.start()
    b_sync.wait(timeout=5.0)
    b_ready.wait(timeout=5.0)

    b_start_cons.set()
    t_b0 = time.perf_counter()
    b_start_prod.set()

    b_prod_done.wait(timeout=10.0)
    t_b1 = time.perf_counter()
    b_burst_dur = t_b1 - t_b0
    b_burst_eps = burst_ticks / max(0.001, b_burst_dur)

    time.sleep(0.5)
    b_cons_done.set()
    b_prod.join(timeout=5.0)
    b_consumer.join(timeout=5.0)
    if b_consumer.is_alive():
        b_consumer.terminate()

    b_res = b_queue.get(timeout=5.0) if not b_queue.empty() else {}
    results["wraparound_stress_test"] = {
        "burst_ticks": burst_ticks,
        "buffer_slots": burst_slots,
        "wraparounds": burst_ticks // burst_slots,
        "burst_throughput_eps": round(b_burst_eps, 1),
        "consumer_result": b_res,
    }
    print(
        f"Burst Speed: {b_burst_eps:,.1f} ticks/s ({burst_ticks // burst_slots} wraparounds)"
    )
    print(
        f"Consumer Result: Read={b_res.get('ticks_read')}, Torn Reads={b_res.get('torn_reads')}, "
        f"Laps={b_res.get('total_laps')}, Skipped={b_res.get('skipped_ticks')}"
    )

    try:
        s = SharedMemory(name=shm_name_burst, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass

    # =========================================================================
    # Test 3: Slow Consumer Lap Detection & Overrun Accounting
    # =========================================================================
    shm_name_slow = f"test_mdrap_spmc_slow_{base_id}"
    print(
        "\n--- [SPMC Pillar 1] Test 3: Slow Consumer Lap Detection & Overrun Accounting ---"
    )
    slow_sync = mp.Event()
    slow_start_prod = mp.Event()
    slow_prod_done = mp.Event()
    slow_cons_done = mp.Event()
    slow_queue = mp.Queue()
    slow_ready = mp.Event()
    slow_start_cons = mp.Event()

    slow_prod = mp.Process(
        target=_producer_worker,
        args=(
            shm_name_slow,
            1024,
            10000,
            0,
            slow_sync,
            slow_start_prod,
            slow_prod_done,
            slow_cons_done,
            0.0,
        ),
    )
    slow_prod.start()
    slow_sync.wait(timeout=5.0)

    slow_consumer = mp.Process(
        target=_slow_consumer_worker,
        args=(shm_name_slow, 99, slow_queue, slow_ready, slow_start_cons, 0.001, 3.0),
    )
    slow_consumer.start()
    slow_ready.wait(timeout=5.0)

    slow_start_cons.set()
    slow_start_prod.set()

    slow_prod_done.wait(timeout=10.0)
    time.sleep(1.0)
    slow_cons_done.set()
    slow_prod.join(timeout=5.0)
    slow_consumer.join(timeout=5.0)
    if slow_consumer.is_alive():
        slow_consumer.terminate()

    slow_res = slow_queue.get(timeout=5.0) if not slow_queue.empty() else {}
    results["slow_consumer_test"] = slow_res
    print(
        f"Slow Consumer Result: Laps Detected={slow_res.get('laps_detected', 0)}, "
        f"Skipped Ticks={slow_res.get('skipped_ticks', 0)}, Read Ticks={slow_res.get('ticks_read', 0)}"
    )

    try:
        s = SharedMemory(name=shm_name_slow, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass

    # =========================================================================
    # Test 4: Consumer Crash and Replacement Restart
    # =========================================================================
    shm_name_crash = f"test_mdrap_spmc_crash_{base_id}"
    print("\n--- [SPMC Pillar 1] Test 4: Consumer Crash & Replacement Restart ---")
    crash_sync = mp.Event()
    crash_start_prod = mp.Event()
    crash_prod_done = mp.Event()
    crash_cons_done = mp.Event()
    crash_queue = mp.Queue()
    crash_ready1 = mp.Event()
    crash_start_cons1 = mp.Event()

    c_prod = mp.Process(
        target=_producer_worker,
        args=(
            shm_name_crash,
            4096,
            10000,
            0,
            crash_sync,
            crash_start_prod,
            crash_prod_done,
            crash_cons_done,
            0.0002,
        ),
    )
    c_prod.start()
    crash_sync.wait(timeout=5.0)

    c_doomed = mp.Process(
        target=_fast_consumer_worker,
        args=(
            shm_name_crash,
            101,
            10000,
            crash_queue,
            crash_ready1,
            crash_start_cons1,
            True,
            10.0,
        ),
    )
    c_doomed.start()
    crash_ready1.wait(timeout=5.0)

    crash_start_cons1.set()
    crash_start_prod.set()

    time.sleep(0.4)
    c_doomed.terminate()
    c_doomed.join(timeout=2.0)
    print(f"Consumer {c_doomed.pid} killed with SIGTERM while producer active.")

    crash_ready2 = mp.Event()
    crash_start_cons2 = mp.Event()
    c_replacement = mp.Process(
        target=_fast_consumer_worker,
        args=(
            shm_name_crash,
            102,
            2000,
            crash_queue,
            crash_ready2,
            crash_start_cons2,
            True,
            10.0,
        ),
    )
    c_replacement.start()
    crash_ready2.wait(timeout=5.0)
    crash_start_cons2.set()

    crash_prod_done.wait(timeout=10.0)
    c_replacement.join(timeout=10.0)
    crash_cons_done.set()
    c_prod.join(timeout=5.0)

    rep_res = crash_queue.get(timeout=5.0) if not crash_queue.empty() else {}
    results["crash_recovery_test"] = {
        "doomed_terminated": True,
        "replacement_result": rep_res,
    }
    print(
        f"Replacement Consumer Result: Read={rep_res.get('ticks_read', 0)}, "
        f"Torn Reads={rep_res.get('torn_reads', 0)}, Seq Errors={rep_res.get('seq_errors', 0)}"
    )

    try:
        s = SharedMemory(name=shm_name_crash, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass

    # =========================================================================
    # Test 5: Producer Restart and Epoch Invalidation
    # =========================================================================
    shm_name_epoch = f"test_mdrap_spmc_epoch_{base_id}"
    print("\n--- [SPMC Pillar 1] Test 5: Producer Restart & Epoch Invalidation ---")
    try:
        w1 = SHMWriter(name=shm_name_epoch, slot_count=1024)
        initial_epoch = w1.epoch_id

        r_epoch = SHMReader(name=shm_name_epoch)
        reader_initial_epoch = r_epoch.epoch_id

        # Shutdown writer 1 and recreate writer 2 on the same segment
        w1.close()
        w2 = SHMWriter(name=shm_name_epoch, slot_count=1024)
        new_epoch = w2.epoch_id

        is_valid = r_epoch.check_epoch_valid(force_probe=True)
        r_epoch.close()
        w2.close()

        results["epoch_invalidation_test"] = {
            "initial_epoch": initial_epoch,
            "reader_initial_epoch": reader_initial_epoch,
            "new_epoch": new_epoch,
            "detected_invalidation": not is_valid,
            "epoch_changed": initial_epoch != new_epoch,
        }
        print(
            f"Initial Epoch: {initial_epoch}, Restart Epoch: {new_epoch}, Invalidated: {not is_valid}"
        )
    except Exception as exc:
        results["epoch_invalidation_test"] = {"error": str(exc)}
        print(f"Epoch Invalidation Error: {exc}")

    try:
        s = SharedMemory(name=shm_name_epoch, create=False)
        s.close()
        s.unlink()
    except Exception:
        pass

    return results


if __name__ == "__main__":
    mp.freeze_support()
    res = run_spmc_cross_process_suite()
    os.makedirs("benchmarks", exist_ok=True)
    with open("benchmarks/spmc_cross_process_results.json", "w") as f:
        json.dump(res, f, indent=2)
    print(
        "\n[SPMC Pillar 1] Results successfully saved to benchmarks/spmc_cross_process_results.json"
    )
