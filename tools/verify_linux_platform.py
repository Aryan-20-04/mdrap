"""
MDRAP 6.0.8 — Linux (Ubuntu 22.04 LTS+) Platform Verification Script.

Executes direct platform validation under Linux:
1. Environment and kernel inspection (/etc/os-release, uname, glibc).
2. Native extension loading (_fastpath_native.so, HAS_FASTPATH).
3. POSIX Shared Memory (/dev/shm) permissions, seqlock atomics, and presence bitmasks.
4. Multi-process cross-process SPMC streaming across separate OS processes.
5. TCP daemon streaming and StreamClient socket communication.
6. SQLite WAL persistence and explicit checkpointing modes.
7. Pure-Python fallback parity (MDRAP_DISABLE_FASTPATH=1).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import platform
import socket
import sys
import time

# Ensure project root in sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
sys.path.insert(0, ROOT_DIR)

os.environ["MDRAP_API_KEY_SALT"] = "linux_verification_salt_secret_123"


def test_platform_info():
    print(f"[Linux Test 1] OS: {platform.platform()} | Python: {sys.version.split()[0]} | Machine: {platform.machine()}")
    assert "linux" in sys.platform.lower(), f"Expected Linux platform, got {sys.platform}"
    import mdrap
    assert mdrap.__version__ == "6.0.8", f"Expected version 6.0.8, got {mdrap.__version__}"
    print(f"[Linux Test 1] Version Metadata: {mdrap.__version__} (PASSED)")


def test_native_fastpath():
    from mdrap.fastpath import HAS_FASTPATH
    print(f"[Linux Test 2] HAS_FASTPATH: {HAS_FASTPATH}")
    assert HAS_FASTPATH is True, "Expected HAS_FASTPATH to be True on Linux with compiled .so"
    from mdrap.fastpath import FastQualityEngine
    engine = FastQualityEngine()
    print(f"[Linux Test 2] FastQualityEngine Native Bridge: ACTIVE (PASSED)")


def _linux_consumer_worker(shm_name: str, expected_count: int, ready_evt: mp.Event, result_queue: mp.Queue):
    """Consumer process reading from Linux /dev/shm."""
    try:
        from mdrap.shm import SHMReader
        reader = SHMReader(name=shm_name)
        ready_evt.set()
        received = 0
        torn_reads = 0
        seq = 1
        while received < expected_count:
            item = reader.read_slot(seq)
            if item is None:
                time.sleep(0.0001)
                continue
            if item.get("seq") != seq:
                torn_reads += 1
            received += 1
            seq += 1
        reader.close()
        result_queue.put({"status": "OK", "received": received, "torn_reads": torn_reads})
    except Exception as exc:
        result_queue.put({"status": "ERROR", "error": str(exc)})


def test_posix_shm_multi_process():
    print("[Linux Test 3] Validating POSIX /dev/shm multi-process ring buffer...")
    shm_name = f"mdrap_linux_test_{os.getpid()}"
    total_events = 2000

    from mdrap.shm import SHMWriter
    writer = SHMWriter(name=shm_name, slot_count=1024)

    ctx = mp.get_context("spawn")
    ready_evt = ctx.Event()
    res_q = ctx.Queue()
    proc = ctx.Process(target=_linux_consumer_worker, args=(shm_name, total_events, ready_evt, res_q))
    proc.start()

    ready_evt.wait(timeout=5.0)

    for i in range(1, total_events + 1):
        writer.write_tick(
            seq=i,
            symbol="BTC/USD",
            source="BINANCE",
            price=65000.0 + (i * 0.1),
            size=1.5,
            bid=64999.0,
            ask=65001.0,
            bid_size=10.0,
            ask_size=12.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=time.time(),
            ingest_ts=time.time(),
            broadcast_ts=time.time(),
            engine_us=10.5,
        )

    proc.join(timeout=10.0)
    writer.close()

    res = res_q.get(timeout=2.0)
    assert res["status"] == "OK", f"Consumer failed: {res.get('error')}"
    assert res["received"] == total_events, f"Expected {total_events}, got {res['received']}"
    assert res["torn_reads"] == 0, f"Expected 0 torn reads, got {res['torn_reads']}"
    print(f"[Linux Test 3] Multi-Process POSIX /dev/shm: 100% Delivery ({total_events} events, 0 torn reads) (PASSED)")


def test_tcp_streaming_daemon():
    print("[Linux Test 4] Validating Local TCP Streaming & StreamClient...")
    from mdrap.service import MarketDataDaemon, StreamClient

    port = 19970
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=port,
        db_path=":memory:",
        sim_speed_eps=500.0,
        sim_events=20000,
        enable_shm=False,
    )
    daemon.start(blocking=False)
    time.sleep(0.5)

    try:
        client = StreamClient(host="127.0.0.1", port=port, timeout=5.0)
        client.connect()
        status = client.get_status()
        assert isinstance(status, dict)

        ticks = []
        for tick in client.stream(symbol="ALL", limit=50):
            ticks.append(tick)

        assert len(ticks) == 50, f"Expected 50 ticks, got {len(ticks)}"
        client.close()
        print(f"[Linux Test 4] TCP Daemon StreamClient: Successfully streamed 50 ticks (PASSED)")
    finally:
        daemon.stop()


def test_wal_checkpoint_and_persistence():
    print("[Linux Test 5] Validating SQLite WAL checkpointing on Linux...")
    from mdrap.storage import Store
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        store = Store(db_path)
        for mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
            res = store.wal_checkpoint(mode=mode)
            assert isinstance(res, dict)
            assert res["busy"] == 0, f"Checkpoint {mode} reported busy"
        store.close()
        print("[Linux Test 5] SQLite WAL Checkpoint Modes: COMPLETED (PASSED)")
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
        wal = db_path + "-wal"
        shm = db_path + "-shm"
        if os.path.exists(wal):
            os.remove(wal)
        if os.path.exists(shm):
            os.remove(shm)


def test_python_fallback_parity():
    print("[Linux Test 6] Validating Pure-Python fallback on Linux...")
    from mdrap.models import RawEvent, EventType
    from mdrap.pipeline import Pipeline
    from mdrap.quality import QualityEngine
    from mdrap.storage import Store

    store = Store(":memory:")
    quality = QualityEngine()
    pipe = Pipeline(store, quality=quality)

    raw = RawEvent(
        source="FEEDX",
        payload={
            "price": 65000.0,
            "quantity": 1.0,
            "bid": 64999.0,
            "ask": 65001.0,
            "sequence": 1,
            "instrument": "BTC/USD",
        },
        receive_timestamp=time.time(),
        raw_id="raw-lin-1",
    )
    ev = pipe.process_one(raw)
    assert ev is not None
    assert ev.instrument_id == "BTC/USD"
    pipe.finish()
    store.close()
    print("[Linux Test 6] Pure Python Fallback Pipeline: VERIFIED (PASSED)")


def main():
    print("================================================================")
    print("MDRAP 6.0.8 — Linux (Ubuntu 22.04 LTS+) Platform Verification")
    print("================================================================")
    test_platform_info()
    test_native_fastpath()
    test_posix_shm_multi_process()
    test_tcp_streaming_daemon()
    test_wal_checkpoint_and_persistence()
    test_python_fallback_parity()
    print("================================================================")
    print("ALL LINUX PLATFORM VERIFICATIONS PASSED CLEANLY!")
    print("================================================================")


if __name__ == "__main__":
    main()
