"""
MDRAP 6.0.8 — Clean Virtualenv & Sidecar Verification Suite.

Tests:
1. Package import and version from clean virtual environment.
2. SHM sidecar consumer example execution against an active producer.
3. TCP socket sidecar consumer example execution against an active daemon.
4. CLI command execution.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import time

PYTHON_EXE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".clean_venv", "Scripts", "python.exe"))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ["MDRAP_API_KEY_SALT"] = "clean_venv_verification_salt_123"


def test_clean_import():
    print("[Clean Venv Test 1] Verifying clean package import...")
    res = subprocess.run(
        [PYTHON_EXE, "-c", "import mdrap; print(mdrap.__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    ver = res.stdout.strip()
    assert ver == "6.0.8", f"Expected version 6.0.8, got {ver}"
    print(f"[Clean Venv Test 1] Package Version 6.0.8 Verified: {ver} (PASSED)")


def _producer_proc(shm_name: str, stop_evt: mp.Event):
    """Produces ticks into shared memory for the SHM consumer example."""
    from mdrap.shm import SHMWriter
    writer = SHMWriter(name=shm_name, slot_count=1024)
    seq = 1
    try:
        while not stop_evt.is_set():
            writer.write_tick(
                seq=seq,
                symbol="BTC/USD",
                source="BINANCE",
                price=65000.0 + (seq % 100),
                size=1.0,
                bid=64999.0,
                ask=65001.0,
                bid_size=5.0,
                ask_size=5.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=time.time(),
                ingest_ts=time.time(),
                broadcast_ts=time.time(),
                engine_us=12.0,
            )
            seq += 1
            time.sleep(0.001)
    finally:
        writer.close()


def test_shm_consumer_example():
    print("[Clean Venv Test 2] Testing examples/sidecar_shm_consumer.py...")
    stop_evt = mp.Event()
    prod = mp.Process(target=_producer_proc, args=("mdrap_feed", stop_evt))
    prod.start()
    time.sleep(0.5)

    try:
        # Run sidecar_shm_consumer.py for max 50 events
        cmd = [
            PYTHON_EXE,
            "-c",
            "import sys, os; sys.path.insert(0, 'src'); from examples.sidecar_shm_consumer import SidecarSHMConsumer; c = SidecarSHMConsumer('mdrap_feed'); c.run(max_events=50)",
        ]
        res = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"SHM sidecar consumer failed: {res.stderr}"
        assert "events" in res.stdout, f"Unexpected output: {res.stdout}"
        print(f"[Clean Venv Test 2] SHM Sidecar Consumer Example: VERIFIED (PASSED)")
    finally:
        stop_evt.set()
        prod.join(timeout=3.0)


def test_socket_consumer_example():
    print("[Clean Venv Test 3] Testing examples/sidecar_socket_consumer.py...")
    from mdrap.service import MarketDataDaemon

    port = 19985
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=port,
        db_path=":memory:",
        sim_speed_eps=500.0,
        sim_events=5000,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.5)

    try:
        cmd = [
            PYTHON_EXE,
            "-c",
            f"import sys, os; sys.path.insert(0, 'src'); from examples.sidecar_socket_consumer import SidecarSocketConsumer; c = SidecarSocketConsumer(host='127.0.0.1', port={port}); c.run(max_events=25)",
        ]
        res = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, timeout=10.0)
        assert res.returncode == 0, f"Socket sidecar consumer failed: {res.stderr}"
        assert "events" in res.stdout, f"Unexpected output: {res.stdout}"
        print(f"[Clean Venv Test 3] TCP Socket Sidecar Consumer Example: VERIFIED (PASSED)")
    finally:
        daemon.stop()


def test_cli_execution():
    print("[Clean Venv Test 4] Testing CLI entry point...")
    res = subprocess.run(
        [PYTHON_EXE, "cli.py", "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "MDRAP PLATFORM COMMAND MATRIX ● v6.0.8" in res.stdout
    print("[Clean Venv Test 4] CLI Execution: VERIFIED (PASSED)")


def main():
    print("================================================================")
    print("MDRAP 6.0.8 — Clean Virtualenv & Sidecar Verification Suite")
    print(f"Target Python: {PYTHON_EXE}")
    print("================================================================")
    test_clean_import()
    test_shm_consumer_example()
    test_socket_consumer_example()
    test_cli_execution()
    print("================================================================")
    print("ALL CLEAN VIRTUALENV TESTS PASSED CLEANLY!")
    print("================================================================")


if __name__ == "__main__":
    main()
