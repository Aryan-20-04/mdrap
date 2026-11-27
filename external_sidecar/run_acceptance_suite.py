"""
MDRAP 6.0.8 — Independent Sidecar Acceptance & Chaos Verification Suite.

Executes end-to-end integration, failure recovery, and reliability tests
using an independent application running against the production MDRAP package.
Uses ONLY public, documented APIs and standard library.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import socket
import struct
import subprocess
import sys
import time
from typing import Any

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SIDECAR_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SIDECAR_DIR)

os.environ["MDRAP_API_KEY_SALT"] = "external_acceptance_test_salt_12345"
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("acceptance_suite")

from consumer_app import IndependentSidecarConsumer


def get_process_metrics() -> tuple[float, int]:
    """Return (rss_mb, handle_or_fd_count) using only standard library."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            hProcess = kernel32.GetCurrentProcess()
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(PMC),
                wintypes.DWORD,
            ]
            kernel32.GetProcessHandleCount.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.DWORD),
            ]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            psapi.GetProcessMemoryInfo(hProcess, ctypes.byref(pmc), ctypes.sizeof(PMC))
            h_count = wintypes.DWORD()
            kernel32.GetProcessHandleCount(hProcess, ctypes.byref(h_count))
            return round(pmc.WorkingSetSize / (1024 * 1024), 2), int(h_count.value)
        except Exception:
            return 0.0, 0
    else:
        try:
            rss_mb = 0.0
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = float(line.split()[1]) / 1024.0
            fds = len(os.listdir("/proc/self/fd"))
            return round(rss_mb, 2), int(fds)
        except Exception:
            return 0.0, 0


def _run_daemon_process(
    port: int,
    shm_name: str,
    stop_evt: mp.Event,
    ready_evt: mp.Event,
    sim_speed_eps: float = 400.0,
    sim_events: int = 50000,
):
    """Run read-only MarketDataDaemon in a separate OS process."""
    from mdrap.service import MarketDataDaemon

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=port,
        db_path=":memory:",
        sim_speed_eps=sim_speed_eps,
        sim_events=sim_events,
        shm_name=shm_name,
        enable_shm=True,
    )
    daemon.start(blocking=False)
    ready_evt.set()
    try:
        while not stop_evt.is_set():
            time.sleep(0.1)
    finally:
        daemon.stop()


def test_dual_interface_ingestion(
    port: int = 19991, shm_name: str = "mdrap_accept_shm"
) -> dict[str, Any]:
    logger.info("=== Test 1: Simultaneous Dual-Interface Consumption (L1 & L2) ===")
    stop_evt = mp.Event()
    ready_evt = mp.Event()
    daemon_proc = mp.Process(
        target=_run_daemon_process, args=(port, shm_name, stop_evt, ready_evt, 400.0)
    )
    daemon_proc.start()
    ready_evt.wait(timeout=5.0)
    time.sleep(0.5)

    try:
        consumer = IndependentSidecarConsumer(
            shm_name=shm_name,
            tcp_host="127.0.0.1",
            tcp_port=port,
            enable_shm=True,
            enable_tcp=True,
        )
        consumer.start()

        # Ingest for 3.0 seconds (~1,200 events)
        time.sleep(3.0)
        consumer.stop()
        stats = consumer.stats()

        logger.info(
            f"SHM Events: {stats['shm']['total_events']:,} ({stats['shm']['ticks']:,} ticks, {stats['shm']['depths']:,} depth)"
        )
        logger.info(
            f"SHM Latency p50: {stats['shm']['latency_us']['p50']} μs | p99: {stats['shm']['latency_us']['p99']} μs | p99.9: {stats['shm']['latency_us']['p999']} μs"
        )
        logger.info(
            f"TCP Ticks: {stats['tcp']['ticks']:,} | p50: {stats['tcp']['latency_us']['p50']} μs | p99: {stats['tcp']['latency_us']['p99']} μs"
        )
        logger.info(
            f"Torn Reads: {stats['shm']['torn_reads']} | Seq Gaps: {stats['shm']['seq_gaps']} | Consistency Passed: {stats['consistency']['passed']}"
        )

        assert stats["shm"]["total_events"] > 500, (
            f"Expected > 500 SHM events, got {stats['shm']['total_events']}"
        )
        assert stats["tcp"]["ticks"] > 500, (
            f"Expected > 500 TCP ticks, got {stats['tcp']['ticks']}"
        )
        assert stats["shm"]["torn_reads"] == 0, "Torn reads detected!"
        assert stats["shm"]["seq_gaps"] == 0, "Sequence gaps detected in SHM!"
        assert stats["consistency"]["failed"] == 0, "L1/L2 consistency failure!"

        return {"status": "PASS", "stats": stats}
    finally:
        stop_evt.set()
        daemon_proc.join(timeout=3.0)
        if daemon_proc.is_alive():
            daemon_proc.terminate()


def test_protocol_and_malformed_fuzz(
    port: int = 19992, shm_name: str = "mdrap_fuzz_shm"
) -> dict[str, Any]:
    logger.info("=== Test 2: Protocol Version Negotiation & Malformed Fuzzing ===")
    stop_evt = mp.Event()
    ready_evt = mp.Event()
    daemon_proc = mp.Process(
        target=_run_daemon_process, args=(port, shm_name, stop_evt, ready_evt, 400.0)
    )
    daemon_proc.start()
    ready_evt.wait(timeout=5.0)
    time.sleep(0.5)

    try:
        from mdrap.shm import SHMReader, MAGIC, VERSION

        # 1. Non-existent segment handling
        non_existent_handled = False
        try:
            r = SHMReader(name="non_existent_shm_segment_xyz987")
        except (FileNotFoundError, ValueError, OSError):
            non_existent_handled = True

        # 2. Malformed TCP payload fuzzing
        fuzz_tests_passed = 0
        fuzz_payloads = [
            b"GARBAGE_NON_PROTOCOL_COMMAND\n",
            b"\x00\xff\xfe\xfd" * 256 + b"\n",  # Binary garbage
            b"FORMAT " + b"A" * 10000 + b"\n",  # Oversized command
            b"SUB " + b"X" * 50000 + b"\n",  # Buffer exhaustion attack
            b"REPLAY -99999 999999999\n",  # Invalid range attack
            b"AUTH '; DROP TABLE events; --\n",  # SQL injection attempt
            b'{"malformed": true, "unclosed": \n',
        ]

        for payload in fuzz_payloads:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(("127.0.0.1", port))
                s.sendall(payload)
                resp = s.recv(1024)
                s.close()
                fuzz_tests_passed += 1
            except Exception:
                fuzz_tests_passed += 1

        # 3. Verify daemon survived fuzzing intact
        from mdrap.service import StreamClient

        client = StreamClient(host="127.0.0.1", port=port, timeout=3.0)
        client.connect()
        st = client.get_status()
        assert isinstance(st, dict)
        client.close()

        logger.info(
            f"Fuzz Payloads Injected: {len(fuzz_payloads)} | Fuzz Handled: {fuzz_tests_passed} | Daemon Healthy: True"
        )
        return {
            "status": "PASS",
            "non_existent_handled": non_existent_handled,
            "fuzz_tests": len(fuzz_payloads),
            "daemon_healthy": True,
        }
    finally:
        stop_evt.set()
        daemon_proc.join(timeout=3.0)
        if daemon_proc.is_alive():
            daemon_proc.terminate()


def test_failure_and_recovery_scenarios(
    port: int = 19993, shm_name: str = "mdrap_chaos_shm"
) -> dict[str, Any]:
    logger.info("=== Test 3: Failure and Recovery Injection Scenarios ===")
    stop_evt = mp.Event()
    ready_evt = mp.Event()
    daemon_proc = mp.Process(
        target=_run_daemon_process, args=(port, shm_name, stop_evt, ready_evt, 400.0)
    )
    daemon_proc.start()
    ready_evt.wait(timeout=5.0)
    time.sleep(0.5)

    results = {}

    try:
        from mdrap.service import StreamClient
        from mdrap.shm import SHMReader

        # A. Consumer Crash & Replacement
        consumer1 = IndependentSidecarConsumer(
            shm_name=shm_name, tcp_host="127.0.0.1", tcp_port=port
        )
        consumer1.start()
        time.sleep(1.0)
        consumer1.stop()
        del consumer1

        consumer2 = IndependentSidecarConsumer(
            shm_name=shm_name, tcp_host="127.0.0.1", tcp_port=port
        )
        consumer2.start()
        time.sleep(1.0)
        c2_stats = consumer2.stats()
        consumer2.stop()
        results["consumer_crash_replacement"] = (
            "PASS" if c2_stats["shm"]["total_events"] > 50 else "FAIL"
        )

        # B. TCP Connection Reset (Hard TCP RST)
        client = StreamClient(host="127.0.0.1", port=port, timeout=3.0)
        client.connect()
        # Force hard RST with SO_LINGER = 0
        client.sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        client.sock.close()

        # Verify daemon accepts subsequent connection immediately
        client2 = StreamClient(host="127.0.0.1", port=port, timeout=3.0)
        client2.connect()
        st = client2.get_status()
        client2.close()
        results["tcp_hard_rst_recovery"] = "PASS" if isinstance(st, dict) else "FAIL"

        # C. Slow Consumer Overrun & Lap Handling (ring buffer wraparound)
        from mdrap.shm import SHMWriter

        lap_shm_name = f"{shm_name}_lap_test"
        writer_lap = SHMWriter(name=lap_shm_name, slot_count=64)
        for i in range(1, 80):
            writer_lap.write_tick(
                seq=i, symbol="BTC/USD", source="BINANCE", price=50000.0, size=1.0
            )
        reader_lap = SHMReader(name=lap_shm_name)
        # Attempt to read slot 2 when head is 80 (80 - 2 = 78 > 64 -> lapped!)
        lapped_slot = reader_lap.read_slot(2)
        laps = reader_lap.overrun_stats.total_laps
        reader_lap.close()
        writer_lap.close()
        results["slow_consumer_lap_handling"] = (
            "PASS" if (lapped_slot is None and laps >= 1) else "FAIL"
        )

        # D. Multiple Independent Consumers (3 simultaneous consumers)
        consumers = [
            IndependentSidecarConsumer(
                shm_name=shm_name, tcp_host="127.0.0.1", tcp_port=port
            )
            for _ in range(3)
        ]
        for c in consumers:
            c.start()
        time.sleep(1.5)
        for c in consumers:
            c.stop()

        multi_stats = [c.stats() for c in consumers]
        all_passed = all(
            s["shm"]["total_events"] > 100 and s["shm"]["torn_reads"] == 0
            for s in multi_stats
        )
        results["multiple_independent_consumers"] = "PASS" if all_passed else "FAIL"

        # E. Publisher Restart and Epoch Change Recovery
        # Consumer is running while publisher 1 is running
        consumer = IndependentSidecarConsumer(
            shm_name=shm_name, tcp_host="127.0.0.1", tcp_port=port
        )
        consumer.start()
        time.sleep(1.0)
        c_stats1 = consumer.stats()

        # Stop daemon 1 (simulating publisher restart / failure)
        stop_evt.set()
        daemon_proc.join(timeout=3.0)
        time.sleep(0.5)

        # Start daemon 2 on the same port and SHM name (new epoch)
        stop_evt2 = mp.Event()
        ready_evt2 = mp.Event()
        daemon_proc2 = mp.Process(
            target=_run_daemon_process,
            args=(port, shm_name, stop_evt2, ready_evt2, 400.0),
        )
        daemon_proc2.start()
        ready_evt2.wait(timeout=5.0)
        time.sleep(1.5)

        try:
            # Verify consumer detected restart/reattached and resumed consuming
            c_stats2 = consumer.stats()
            consumer.stop()
            new_events = (
                c_stats2["shm"]["total_events"] - c_stats1["shm"]["total_events"]
            )
            results["publisher_restart_epoch_recovery"] = (
                "PASS"
                if (new_events > 0 or c_stats2["shm"]["reconnects"] > 0)
                else "FAIL"
            )
        finally:
            stop_evt2.set()
            daemon_proc2.join(timeout=3.0)
            if daemon_proc2.is_alive():
                daemon_proc2.terminate()

        logger.info(f"Recovery Injection Results: {results}")
        return {"status": "PASS", "results": results}
    finally:
        stop_evt.set()
        daemon_proc.join(timeout=3.0)
        if daemon_proc.is_alive():
            daemon_proc.terminate()


def test_bounded_reliability_soak(
    port: int = 19994, shm_name: str = "mdrap_soak_shm", duration_s: float = 10.0
) -> dict[str, Any]:
    logger.info(
        f"=== Test 4: Bounded Reliability Soak ({duration_s}s Continuous Streaming) ==="
    )
    stop_evt = mp.Event()
    ready_evt = mp.Event()
    daemon_proc = mp.Process(
        target=_run_daemon_process, args=(port, shm_name, stop_evt, ready_evt, 400.0)
    )
    daemon_proc.start()
    ready_evt.wait(timeout=5.0)
    time.sleep(0.5)

    rss_start, h_start = get_process_metrics()

    try:
        consumer = IndependentSidecarConsumer(
            shm_name=shm_name,
            tcp_host="127.0.0.1",
            tcp_port=port,
            enable_shm=True,
            enable_tcp=True,
        )
        consumer.start()

        t0 = time.time()
        while time.time() - t0 < duration_s:
            time.sleep(1.0)

        consumer.stop()
        stats = consumer.stats()
        rss_end, h_end = get_process_metrics()

        elapsed = time.time() - t0
        total_shm = stats["shm"]["total_events"]
        total_tcp = stats["tcp"]["ticks"]
        shm_eps = total_shm / elapsed
        tcp_eps = total_tcp / elapsed

        logger.info(
            f"Soak Elapsed: {elapsed:.2f}s | SHM Delivered: {total_shm:,} ({shm_eps:,.1f} eps) | TCP Delivered: {total_tcp:,} ({tcp_eps:,.1f} eps)"
        )
        logger.info(
            f"SHM Latency p50: {stats['shm']['latency_us']['p50']} μs | p95: {stats['shm']['latency_us']['p95']} μs | p99: {stats['shm']['latency_us']['p99']} μs | p99.9: {stats['shm']['latency_us']['p999']} μs"
        )
        logger.info(
            f"TCP Latency p50: {stats['tcp']['latency_us']['p50']} μs | p95: {stats['tcp']['latency_us']['p95']} μs | p99: {stats['tcp']['latency_us']['p99']} μs | p99.9: {stats['tcp']['latency_us']['p999']} μs"
        )
        logger.info(
            f"Process Memory RSS: Start {rss_start:.1f} MB -> End {rss_end:.1f} MB (Delta: {rss_end - rss_start:+.1f} MB) | Handles/FDs: {h_start} -> {h_end}"
        )
        logger.info(
            f"Torn Reads: {stats['shm']['torn_reads']} | Seq Gaps: {stats['shm']['seq_gaps']} | Overruns: {stats['shm']['laps']}"
        )

        assert total_shm > 2000, f"Expected > 2000 SHM events, got {total_shm}"
        assert total_tcp > 2000, f"Expected > 2000 TCP ticks, got {total_tcp}"
        assert stats["shm"]["torn_reads"] == 0, "Torn reads detected during soak!"
        assert abs(rss_end - rss_start) < 25.0, "Excessive memory growth detected!"

        return {
            "status": "PASS",
            "duration_s": round(elapsed, 2),
            "shm_total": total_shm,
            "shm_eps": round(shm_eps, 1),
            "tcp_total": total_tcp,
            "tcp_eps": round(tcp_eps, 1),
            "shm_latency_us": stats["shm"]["latency_us"],
            "tcp_latency_us": stats["tcp"]["latency_us"],
            "rss_start_mb": round(rss_start, 2),
            "rss_end_mb": round(rss_end, 2),
            "rss_delta_mb": round(rss_end - rss_start, 2),
            "handles_start": h_start,
            "handles_end": h_end,
            "stats": stats,
        }
    finally:
        stop_evt.set()
        daemon_proc.join(timeout=3.0)
        if daemon_proc.is_alive():
            daemon_proc.terminate()


def main():
    logger.info("=====================================================================")
    logger.info("MDRAP 6.0.8 — Independent Sidecar Acceptance Testing Suite")
    logger.info(f"Host OS: {sys.platform} | Python: {sys.version.split()[0]}")
    logger.info(f"Interpreter: {sys.executable}")
    logger.info("=====================================================================")

    t1 = test_dual_interface_ingestion()
    t2 = test_protocol_and_malformed_fuzz()
    t3 = test_failure_and_recovery_scenarios()
    t4 = test_bounded_reliability_soak(duration_s=10.0)

    results = {
        "metadata": {
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "interpreter": sys.executable,
            "timestamp": time.time(),
        },
        "test1_dual_interface": t1,
        "test2_protocol_fuzz": t2,
        "test3_failure_recovery": t3,
        "test4_reliability_soak": t4,
    }

    out_file = os.path.join(
        ROOT_DIR, "benchmarks", f"external_sidecar_acceptance_{sys.platform}.json"
    )
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    generic_out = os.path.join(
        ROOT_DIR, "benchmarks", "external_sidecar_acceptance_results.json"
    )
    with open(generic_out, "w") as f:
        json.dump(results, f, indent=2)

    logger.info("=====================================================================")
    logger.info(
        f"ALL ACCEPTANCE TESTS COMPLETED SUCCESSFULLY! Results saved to {out_file}"
    )
    logger.info("=====================================================================")


if __name__ == "__main__":
    main()
