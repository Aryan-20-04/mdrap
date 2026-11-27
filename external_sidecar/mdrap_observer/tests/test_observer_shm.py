"""Tests for Observer Shared Memory (SHM) transport, multi-consumer, and ring wraparound."""

import os
import sys
import threading
import time
import pytest

import mdrap.shm as shm_mod
from mdrap.shm import (
    CANONICAL_SOURCE_TO_WIRE_V3,
    HEADER_SIZE,
    SLOT_SIZE,
    SHMReader,
    SHMWriter,
    VERSION,
    WIRE_TO_CANONICAL_SOURCE_V3,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.client import ObserverClient
from observer.state import ObserverState, VENUES


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def test_observer_shm_end_to_end():
    """Verify ObserverClient in SHM mode reads live tick data from lock-free ring buffer."""
    ring_name = f"test_obs_shm_{int(time.time() * 1000) % 1000000}"
    writer = SHMWriter(name=ring_name, slot_count=256)

    received_events = []

    def on_event(evt):
        received_events.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_event,
    )
    client.start()

    try:
        # Give reader worker time to attach
        time.sleep(0.1)

        # Write 20 ticks into SHM
        for i in range(1, 21):
            writer.write_tick(
                seq=i,
                symbol="BTCUSDT",
                source="BINANCE",
                price=65000.0 + i,
                size=1.0,
                bid=64999.0 + i,
                ask=65001.0 + i,
            )

        # Allow consumer loop to read batch
        time.sleep(0.3)

        assert len(received_events) >= 20
        assert received_events[0]["sym"] == "BTCUSDT"
        assert received_events[0]["seq"] == 1
        assert received_events[-1]["seq"] == 20
        assert client.connected
    finally:
        client.stop()
        writer.close()


def test_observer_shm_ring_wraparound():
    """Verify slow consumer surviving ring buffer wraparound when writer exceeds slot capacity."""
    ring_name = f"test_obs_wrap_{int(time.time() * 1000) % 1000000}"
    # Small slot count of 64 slots
    writer = SHMWriter(name=ring_name, slot_count=64)

    # Pre-fill and wrap around by writing 150 ticks before reader connects
    for i in range(1, 151):
        writer.write_tick(
            seq=i,
            symbol="BTC-USD",
            source="COINBASE",
            price=60000.0 + i,
            size=0.5,
            bid=59999.0 + i,
            ask=60001.0 + i,
        )

    received_events = []

    def on_event(evt):
        received_events.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_event,
    )
    client.start()

    try:
        time.sleep(0.3)
        # Write another 10 ticks while client is active
        for i in range(151, 161):
            writer.write_tick(
                seq=i,
                symbol="BTC-USD",
                source="COINBASE",
                price=60000.0 + i,
                size=0.5,
                bid=59999.0 + i,
                ask=60001.0 + i,
            )
        time.sleep(0.3)

        # Client should have received recent slots without crashing
        assert len(received_events) > 0
        assert client.connected
    finally:
        client.stop()
        writer.close()


def test_observer_multiple_concurrent_consumers():
    """Verify multiple independent Observer consumers read concurrently without interference."""
    ring_name = f"test_obs_multi_{int(time.time() * 1000) % 1000000}"
    writer = SHMWriter(name=ring_name, slot_count=256)

    c1_events = []
    c2_events = []

    c1 = ObserverClient(mode="shm", ring_name=ring_name, on_event_callback=lambda e: c1_events.append(e))
    c2 = ObserverClient(mode="shm", ring_name=ring_name, on_event_callback=lambda e: c2_events.append(e))

    c1.start()
    c2.start()

    try:
        time.sleep(0.1)
        for i in range(1, 31):
            writer.write_tick(
                seq=i,
                symbol="ETH-USD",
                source="KRAKEN",
                price=3400.0 + i,
                size=2.0,
                bid=3399.0 + i,
                ask=3401.0 + i,
            )

        time.sleep(0.4)

        # Both independent consumers must receive the full stream
        assert len(c1_events) >= 30
        assert len(c2_events) >= 30
        assert c1_events[0]["seq"] == 1
        assert c2_events[0]["seq"] == 1
    finally:
        c1.stop()
        c2.stop()
        writer.close()


# ---------------------------------------------------------------------------
# Finding A: Dead-writer busy-spin remediation tests
# ---------------------------------------------------------------------------


def test_observer_shm_dead_writer_no_busy_spin():
    """Closing SHMWriter while ObserverClient consumes must stop CPU spin and mark disconnected."""
    ring_name = f"test_obs_dead_spin_{os.getpid()}_{time.time_ns()}"
    writer = SHMWriter(name=ring_name, slot_count=64)
    received = []
    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=received.append,
        shm_writer_dead_timeout=0.3,
    )
    client.start()

    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)
        for i in range(1, 6):
            writer.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=65000.0 + i, size=1.0)
        assert _wait_until(lambda: len(received) == 5, timeout=2.0)

        # Close writer while client is actively consuming
        writer.close()

        # Client must detect dead writer promptly and drop connected state
        assert _wait_until(lambda: not client.connected, timeout=2.0)
        assert client.reconnect_count >= 1

        # Measure process CPU time over >= 0.6s wall while writer remains absent
        cpu_before = time.process_time()
        wall_before = time.perf_counter()
        time.sleep(0.65)
        cpu_delta = time.process_time() - cpu_before
        wall_delta = time.perf_counter() - wall_before

        assert wall_delta >= 0.6
        assert not client.connected
        assert cpu_delta < 0.15, f"Dead-writer busy-spin detected: cpu_delta={cpu_delta:.4f}s in {wall_delta:.4f}s"

        # stop() must wake backoff immediately (well under 2.0s max backoff)
        t_stop0 = time.perf_counter()
        client.stop()
        stop_elapsed = time.perf_counter() - t_stop0
        assert stop_elapsed < 0.5, f"client.stop() blocked during backoff: {stop_elapsed:.3f}s"
    finally:
        client.stop()
        writer.close()


# ---------------------------------------------------------------------------
# Finding B: Publisher restart initial-tick loss remediation tests
# ---------------------------------------------------------------------------


def test_observer_shm_restart_fully_retained_backlog():
    """Restart with 10 ticks written on new epoch before/during reattach delivers all 10 ticks."""
    ring_name = f"test_obs_restart_full_{os.getpid()}_{time.time_ns()}"
    writer1 = SHMWriter(name=ring_name, slot_count=64)
    received = []
    lock = threading.Lock()

    def on_evt(evt):
        with lock:
            received.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_evt,
        shm_writer_dead_timeout=0.3,
    )
    client.start()

    writer2 = None
    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)
        for i in range(1, 6):
            writer1.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=60000.0 + i, size=1.0)
        assert _wait_until(lambda: len(received) == 5, timeout=2.0)

        # Restart publisher and pre-write 10 ticks on epoch 2 before client reattaches
        writer1.close()
        writer2 = SHMWriter(name=ring_name, slot_count=64)
        for i in range(1, 11):
            writer2.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=70000.0 + i, size=1.0)

        assert _wait_until(lambda: len(received) == 15, timeout=3.0)
        epoch2_prices = [e["price"] for e in received[5:]]
        assert epoch2_prices == [70000.0 + i for i in range(1, 11)]
        epoch2_seqs = [e["seq"] for e in received[5:]]
        assert epoch2_seqs == list(range(1, 11))
    finally:
        client.stop()
        writer1.close()
        if writer2 is not None:
            writer2.close()


def test_observer_shm_restart_partially_overwritten_backlog():
    """Restart with slot_count=16 and 25 ticks pre-written delivers retained 16 and tracks overrun."""
    ring_name = f"test_obs_restart_overrun_{os.getpid()}_{time.time_ns()}"
    writer1 = SHMWriter(name=ring_name, slot_count=16)
    received = []
    lock = threading.Lock()

    def on_evt(evt):
        with lock:
            received.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_evt,
        shm_writer_dead_timeout=0.3,
    )
    client.start()

    writer2 = None
    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)
        writer1.write_tick(seq=1, symbol="ETHUSDT", source="KRAKEN", price=3000.0, size=1.0)
        assert _wait_until(lambda: len(received) == 1, timeout=2.0)

        # Close writer1 and wait until client sees disconnect so writer2 writes 25 ticks before attach
        writer1.close()
        assert _wait_until(lambda: not client.connected, timeout=2.0)

        writer2 = SHMWriter(name=ring_name, slot_count=16)
        for i in range(1, 26):
            writer2.write_tick(seq=i, symbol="ETHUSDT", source="KRAKEN", price=4000.0 + i, size=1.0)

        # Retained ring window for head=26, slot_count=16 is seqs 10..25 (16 ticks)
        assert _wait_until(lambda: len(received) == 17, timeout=3.0)
        epoch2_seqs = [e["seq"] for e in received[1:]]
        assert epoch2_seqs == list(range(10, 26))
        assert client.shm_overrun_laps >= 1
        assert client.shm_skipped_ticks >= 10
    finally:
        client.stop()
        writer1.close()
        if writer2 is not None:
            writer2.close()


def test_observer_shm_restart_empty_epoch_then_ticks():
    """Restart with head==0 (no available events) attaches cleanly and receives subsequent ticks."""
    ring_name = f"test_obs_restart_empty_{os.getpid()}_{time.time_ns()}"
    writer1 = SHMWriter(name=ring_name, slot_count=64)
    received = []
    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=received.append,
        shm_writer_dead_timeout=1.0,
    )
    client.start()

    writer2 = None
    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)
        writer1.write_tick(seq=1, symbol="BTCUSDT", source="BINANCE", price=61000.0, size=1.0)
        assert _wait_until(lambda: len(received) == 1, timeout=2.0)

        writer1.close()
        writer2 = SHMWriter(name=ring_name, slot_count=64)
        # Wait until client is attached to writer2 while head == 0
        assert _wait_until(
            lambda: client.connected
            and client._current_reader is not None
            and client._current_reader.epoch_id == writer2.epoch_id,
            timeout=2.0,
        )
        time.sleep(0.25)
        assert client.connected
        assert len(received) == 1

        # Subsequent ticks starting at seq=1 must all arrive cleanly
        for i in range(1, 6):
            writer2.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=62000.0 + i, size=1.0)

        assert _wait_until(lambda: len(received) == 6, timeout=2.0)
        assert [e["seq"] for e in received[1:]] == [1, 2, 3, 4, 5]
        assert [e["price"] for e in received[1:]] == [62001.0, 62002.0, 62003.0, 62004.0, 62005.0]
    finally:
        client.stop()
        writer1.close()
        if writer2 is not None:
            writer2.close()


def test_observer_shm_multiple_consecutive_epoch_transitions():
    """Multiple consecutive epoch transitions (epoch1 -> epoch2 -> epoch3) deliver all ticks."""
    ring_name = f"test_obs_multi_epoch_{os.getpid()}_{time.time_ns()}"
    writer1 = SHMWriter(name=ring_name, slot_count=64)
    received = []
    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=received.append,
        shm_writer_dead_timeout=0.25,
    )
    client.start()

    writer2 = None
    writer3 = None
    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)
        for i in range(1, 4):
            writer1.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=1000.0 + i, size=1.0)
        assert _wait_until(lambda: len(received) == 3, timeout=2.0)

        # Transition 1 -> 2
        writer1.close()
        writer2 = SHMWriter(name=ring_name, slot_count=64)
        for i in range(1, 5):
            writer2.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=2000.0 + i, size=1.0)
        assert _wait_until(lambda: len(received) == 7, timeout=3.0)

        # Transition 2 -> 3
        writer2.close()
        writer3 = SHMWriter(name=ring_name, slot_count=64)
        for i in range(1, 6):
            writer3.write_tick(seq=i, symbol="BTCUSDT", source="BINANCE", price=3000.0 + i, size=1.0)
        assert _wait_until(lambda: len(received) == 12, timeout=3.0)

        assert [e["price"] for e in received] == (
            [1001.0, 1002.0, 1003.0]
            + [2001.0, 2002.0, 2003.0, 2004.0]
            + [3001.0, 3002.0, 3003.0, 3004.0, 3005.0]
        )
    finally:
        client.stop()
        writer1.close()
        if writer2 is not None:
            writer2.close()
        if writer3 is not None:
            writer3.close()


def test_observer_shm_reader_races_writer_startup():
    """ObserverClient starting before SHMWriter attaches cleanly and captures initial ticks."""
    ring_name = f"test_obs_race_start_{os.getpid()}_{time.time_ns()}"
    received = []
    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=received.append,
        shm_writer_dead_timeout=1.0,
    )
    # Start reader BEFORE writer exists
    client.start()
    writer = None
    try:
        time.sleep(0.15)
        assert not client.connected
        assert client.reconnect_count == 0

        # Now start writer and immediately publish ticks starting at seq=1
        writer = SHMWriter(name=ring_name, slot_count=64)
        for i in range(1, 11):
            writer.write_tick(seq=i, symbol="BTCUSDT", source="DATABENTO", price=65000.0 + i, size=1.0)

        assert _wait_until(lambda: len(received) == 10, timeout=3.0)
        assert client.connected
        assert [e["seq"] for e in received] == list(range(1, 11))
        assert all(e["source"] == "DATABENTO" for e in received)
    finally:
        client.stop()
        if writer is not None:
            writer.close()


# ---------------------------------------------------------------------------
# Finding C: 8-byte source truncation & DATABENTO identity tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("disable_fastpath", [False, True])
def test_observer_shm_10000_events_five_venues_databento_parity(monkeypatch, disable_fastpath):
    """10,000 events across 5 venues preserve exact DATABENTO identity, 0 gaps, and fastpath parity."""
    if disable_fastpath:
        monkeypatch.setattr(shm_mod, "fastpath", None)

    assert HEADER_SIZE == 128
    assert SLOT_SIZE == 128
    assert VERSION == 3
    assert len(CANONICAL_SOURCE_TO_WIRE_V3) == len(WIRE_TO_CANONICAL_SOURCE_V3)

    ring_name = f"test_obs_10k_{int(disable_fastpath)}_{os.getpid()}_{time.time_ns()}"
    writer = SHMWriter(name=ring_name, slot_count=16384)
    state = ObserverState()
    received_count = 0

    def on_evt(evt):
        nonlocal received_count
        received_count += 1
        state.process_event(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_evt,
        shm_writer_dead_timeout=2.0,
    )
    client.start()

    try:
        assert _wait_until(lambda: client.connected, timeout=2.0)

        total_events = 10_000
        for seq in range(1, total_events + 1):
            venue = VENUES[(seq - 1) % len(VENUES)]
            writer.write_tick(
                seq=seq,
                symbol="BTC-USD",
                source=venue,
                price=65000.0 + (seq % 100) * 0.1,
                size=1.0,
                bid=64999.0,
                ask=65001.0,
                bid_size=2.0,
                ask_size=2.0,
                status="VALID",
                exchange_ts=1700000000.0 + seq * 0.001,
                ingest_ts=1700000000.0 + seq * 0.001 + 0.0001,
                broadcast_ts=1700000000.0 + seq * 0.001 + 0.0002,
                engine_us=5.0,
            )

        assert _wait_until(lambda: received_count == total_events, timeout=10.0)

        assert state.stream_health.events_received == total_events
        assert state.stream_health.gaps == 0
        assert state.stream_health.duplicates == 0
        assert state.stream_health.out_of_order == 0
        assert state.health["DATABENTO"].events_received == 2000
        for venue in VENUES:
            assert state.health[venue].events_received == 2000
            assert state.health[venue].feed_gaps == 0
    finally:
        client.stop()
        writer.close()


def test_shm_source_rejects_unregistered_and_reserved_prefix():
    """Unregistered >8-byte sources and '@MDRP' spoofed inputs must raise ValueError."""
    ring_name = f"test_obs_src_val_{os.getpid()}_{time.time_ns()}"
    writer = SHMWriter(name=ring_name, slot_count=64)
    reader = SHMReader(name=ring_name)
    try:
        with pytest.raises(ValueError, match="exceeds 8-byte"):
            writer.write_tick(seq=1, symbol="BTCUSDT", source="UNKNOWN_LONG_SOURCE", price=100.0, size=1.0)

        with pytest.raises(ValueError, match="reserved wire prefix"):
            writer.write_tick(seq=2, symbol="BTCUSDT", source="@MDRP001", price=100.0, size=1.0)

        with pytest.raises(ValueError, match="reserved wire prefix"):
            writer.write_tick(seq=3, symbol="BTCUSDT", source="@MDRPXYZ", price=100.0, size=1.0)

        # Registered >8-byte source round-trips accurately
        writer.write_tick(seq=4, symbol="BTCUSDT", source="DATABENTO", price=65000.0, size=1.0)
        slot = reader.read_slot(4)
        assert slot is not None
        assert slot["source"] == "DATABENTO"
    finally:
        reader.close()
        writer.close()


def _init_raw_v3_ring(name: str, slot_count: int = 128):
    """Allocate a fresh v3 SHM writer and reader for direct parity checks."""
    writer = SHMWriter(name=name, slot_count=slot_count)
    reader = SHMReader(name=name)
    return writer, reader


def test_shm_v3_layout_and_all_registered_sources_parity(monkeypatch):
    """All CANONICAL_SOURCE_TO_WIRE_V3 entries round-trip cleanly across Python, C ext, and ctypes."""
    import ctypes
    import mdrap.fastpath as fp_mod

    assert VERSION == 3
    assert HEADER_SIZE == 128
    assert SLOT_SIZE == 128
    assert ctypes.sizeof(fp_mod.NativeShmSlotV3) == 128
    assert "EQUITIES (SIM)" in CANONICAL_SOURCE_TO_WIRE_V3
    assert "CRYPTO (SIM)" in CANONICAL_SOURCE_TO_WIRE_V3

    canonical_sources = list(CANONICAL_SOURCE_TO_WIRE_V3.keys())
    slot_count = 128

    # 1. Pure Python writer & reader
    ring_py = f"test_ws_c_py_{os.getpid()}_{time.time_ns()}"
    w_py, r_py = _init_raw_v3_ring(ring_py, slot_count=slot_count)
    try:
        with monkeypatch.context() as mp:
            mp.setattr(shm_mod, "fastpath", None)
            for idx, src in enumerate(canonical_sources):
                w_py.write_tick(seq=idx, symbol="BTC-USD", source=src, price=100.0 + idx, size=1.0)
                item = r_py.read_slot(idx)
                assert item is not None
                assert item["source"] == src
    finally:
        r_py.close()
        w_py.close()

    # 2. Direct C-extension + native_shm_* (both _C_EXT and ctypes paths)
    if fp_mod.has_native_shm():
        ring_c = f"test_ws_c_native_{os.getpid()}_{time.time_ns()}"
        w_c, r_c = _init_raw_v3_ring(ring_c, slot_count=slot_count)
        try:
            for idx, src in enumerate(canonical_sources):
                ok = fp_mod.native_shm_write_tick(
                    w_c.shm.buf, slot_count, idx, "BTC-USD", src, price=200.0 + idx, size=2.0
                )
                assert ok is True
                item_ext = fp_mod.native_shm_read_slot(r_c.shm.buf, slot_count, idx)
                assert item_ext is not None
                assert item_ext["source"] == src

                # Force ctypes path (bypass _C_EXT on read) when native DLL is loaded
                if fp_mod._NATIVE_LIB and hasattr(fp_mod._NATIVE_LIB, "fastpath_shm_read_slot_v3"):
                    with monkeypatch.context() as mp:
                        mp.setattr(fp_mod, "_C_EXT", None)
                        item_ct = fp_mod.native_shm_read_slot(r_c.shm.buf, slot_count, idx)
                        assert item_ct == item_ext
        finally:
            r_c.close()
            w_c.close()

        # Also test ctypes native_shm_write_tick directly with _C_EXT disabled when native DLL is loaded
        if fp_mod._NATIVE_LIB and hasattr(fp_mod._NATIVE_LIB, "fastpath_shm_write_tick_v3"):
            ring_ct = f"test_ws_c_ctypes_w_{os.getpid()}_{time.time_ns()}"
            w_ct, r_ct = _init_raw_v3_ring(ring_ct, slot_count=slot_count)
            try:
                with monkeypatch.context() as mp:
                    mp.setattr(fp_mod, "_C_EXT", None)
                    for idx, src in enumerate(["DATABENTO", "EQUITIES (SIM)", "CRYPTO (SIM)", "POLYGON-FINRA_TRF"]):
                        ok = fp_mod.native_shm_write_tick(
                            w_ct.shm.buf, slot_count, idx, "ETH-USD", src, price=300.0 + idx, size=1.0
                        )
                        assert ok is True
                        got = r_ct.read_slot(idx)
                        assert got is not None
                        assert got["source"] == src
            finally:
                r_ct.close()
                w_ct.close()


def test_shm_v3_direct_native_rejects_spoof_and_unknown_sources(monkeypatch):
    """Python, _C_EXT, ctypes, and C DLL must all reject @MDRP* and unregistered >8B sources."""
    import ctypes
    import mdrap.fastpath as fp_mod

    ring_name = f"test_ws_c_rej_{os.getpid()}_{time.time_ns()}"
    writer, reader = _init_raw_v3_ring(ring_name, slot_count=64)
    bad_sources = ["@MDRP001", "@MDRPXYZ", "UNKNOWN_LONG_SOURCE", "BINANC\u00c9"]
    try:
        for bad in bad_sources:
            with pytest.raises(ValueError):
                writer.write_tick(seq=0, symbol="BTC-USD", source=bad, price=100.0, size=1.0)
            if fp_mod.has_native_shm():
                with pytest.raises(ValueError):
                    fp_mod.native_shm_write_tick(
                        writer.shm.buf, 64, 0, "BTC-USD", bad, price=100.0, size=1.0
                    )
                if fp_mod._NATIVE_LIB and hasattr(fp_mod._NATIVE_LIB, "fastpath_shm_write_tick_v3"):
                    with monkeypatch.context() as mp:
                        mp.setattr(fp_mod, "_C_EXT", None)
                        with pytest.raises(ValueError):
                            fp_mod.native_shm_write_tick(
                                writer.shm.buf, 64, 0, "BTC-USD", bad, price=100.0, size=1.0
                            )
                if fp_mod._C_EXT and hasattr(fp_mod._C_EXT, "shm_write_tick_v3"):
                    with pytest.raises(ValueError):
                        fp_mod._C_EXT.shm_write_tick_v3(
                            writer.shm.buf, 64, 0, "BTC-USD", bad,
                            100.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1, 0, 0x03, 1.0, 1.0, 1.0, 1.0
                        )
        # Direct C DLL checks (must return -3 without touching head=0)
        if fp_mod._NATIVE_LIB and hasattr(fp_mod._NATIVE_LIB, "fastpath_shm_write_tick_v3"):
            raw_addr = fp_mod.get_buffer_address(writer.shm.buf)
            rc = fp_mod._NATIVE_LIB.fastpath_shm_write_tick_v3(
                ctypes.c_void_p(raw_addr),
                ctypes.c_size_t(128 + 64 * 128),
                ctypes.c_uint32(64),
                ctypes.c_uint64(0),
                b"BTC-USD",
                b"UNKNOWN_LONG_SOURCE",
                100.0, 1.0, 0.0, 0.0, 0.0, 0.0,
                1, 0, 0x03, 1.0, 1.0, 1.0, 1.0,
            )
            assert rc == -3
            assert reader.read_latest_seq() == 0
    finally:
        reader.close()
        writer.close()


def test_shm_v3_depth_key_parity_python_c_ext_and_ctypes(monkeypatch):
    """DEPTH slots must expose identical keys and None/empty-book values across all readers."""
    import mdrap.fastpath as fp_mod

    ring_name = f"test_ws_c_depth_{os.getpid()}_{time.time_ns()}"
    writer, reader = _init_raw_v3_ring(ring_name, slot_count=64)
    try:
        t0 = 1700000100.0
        writer.write_depth(
            seq=0,
            symbol="ETH/USD",
            best_bid=3000.0,
            best_ask=3001.5,
            bid_size=10.0,
            ask_size=12.0,
            micro_price=3000.68,
            ofi=0.12,
            is_crossed=False,
            exchange_ts=t0,
            ingest_ts=t0 + 0.001,
            broadcast_ts=t0 + 0.002,
            engine_us=4.5,
            status="VALID",
        )
        writer.write_depth(
            seq=1,
            symbol="SOL/USD",
            best_bid=None,
            best_ask=150.25,
            bid_size=None,
            ask_size=None,
            micro_price=None,
            ofi=None,
            is_crossed=True,
            exchange_ts=t0 + 1.0,
            ingest_ts=t0 + 1.001,
            broadcast_ts=t0 + 1.002,
            engine_us=6.25,
            status="SUSPICIOUS",
        )

        with monkeypatch.context() as mp:
            mp.setattr(shm_mod, "fastpath", None)
            py0 = reader.read_slot(0)
            py1 = reader.read_slot(1)

        assert py0 is not None and py1 is not None
        assert set(py0.keys()) == {
            "type", "seq", "sym", "micro_price", "ofi", "bid", "ask",
            "bid_size", "ask_size", "bids", "asks", "is_crossed",
            "status", "exchange_ts", "ingest_ts", "broadcast_ts", "engine_us",
        }
        assert "source" not in py0
        assert "price" not in py0
        assert "size" not in py0
        assert py1["micro_price"] is None
        assert py1["ofi"] is None
        assert py1["bid"] is None
        assert py1["bids"] == []
        assert py1["asks"] == [[150.25, None, "AGG"]]

        if fp_mod.has_native_shm():
            c0 = fp_mod.native_shm_read_slot(reader.shm.buf, 64, 0)
            c1 = fp_mod.native_shm_read_slot(reader.shm.buf, 64, 1)
            assert set(c0.keys()) == set(py0.keys())
            assert set(c1.keys()) == set(py1.keys())
            assert c0 == py0
            assert c1 == py1

            if fp_mod._NATIVE_LIB and hasattr(fp_mod._NATIVE_LIB, "fastpath_shm_read_slot_v3"):
                with monkeypatch.context() as mp:
                    mp.setattr(fp_mod, "_C_EXT", None)
                    ct0 = fp_mod.native_shm_read_slot(reader.shm.buf, 64, 0)
                    ct1 = fp_mod.native_shm_read_slot(reader.shm.buf, 64, 1)
                    assert ct0 == py0
                    assert ct1 == py1
    finally:
        reader.close()
        writer.close()


def test_shm_v3_symbol_utf8_boundary_and_capacity_parity(monkeypatch):
    """Multi-byte UTF-8 <=16B round-trips losslessly; >16B rejects safely; corrupt bytes keep 'sym'."""
    import struct
    import mdrap.fastpath as fp_mod

    ring_name = f"test_ws_c_utf8_{os.getpid()}_{time.time_ns()}"
    writer, reader = _init_raw_v3_ring(ring_name, slot_count=64)
    try:
        valid_syms = ["BTC\u20ac", "\u20ac\u20ac\u20ac\u20ac\u20acA", "ETH-\u20bf"]  # 6B, 16B, 7B UTF-8
        seq = 0
        for sym in valid_syms:
            with monkeypatch.context() as mp:
                mp.setattr(shm_mod, "fastpath", None)
                writer.write_tick(seq=seq, symbol=sym, source="BINANCE", price=10.0, size=1.0)
                py_evt = reader.read_slot(seq)
                assert py_evt is not None
                assert py_evt["sym"] == sym
            if fp_mod.has_native_shm():
                c_evt = fp_mod.native_shm_read_slot(reader.shm.buf, 64, seq)
                assert c_evt is not None and c_evt["sym"] == sym
                writer.write_tick(seq=seq + 1, symbol=sym, source="DATABENTO", price=11.0, size=1.0)
                n_evt = reader.read_slot(seq + 1)
                assert n_evt is not None and n_evt["sym"] == sym and n_evt["source"] == "DATABENTO"
                seq += 2
            else:
                seq += 1

        # Reject symbols exceeding 16 UTF-8 bytes or straddling byte 16 boundary
        bad_syms = ["A" * 14 + "\u20ac", "VERYLONGSYMBOL_OVER_16", "BAD\x00SYM"]
        for bad_sym in bad_syms:
            with pytest.raises(ValueError):
                writer.write_tick(seq=seq, symbol=bad_sym, source="BINANCE", price=10.0, size=1.0)
            with pytest.raises(ValueError):
                writer.write_depth(
                    seq=seq, symbol=bad_sym, best_bid=1.0, best_ask=2.0,
                    bid_size=1.0, ask_size=1.0, micro_price=1.5, ofi=0.0,
                    is_crossed=False, exchange_ts=1.0, ingest_ts=1.0,
                    broadcast_ts=1.0, engine_us=1.0,
                )
            if fp_mod.has_native_shm():
                with pytest.raises(ValueError):
                    fp_mod.native_shm_write_tick(
                        writer.shm.buf, 64, seq, bad_sym, "BINANCE", price=10.0, size=1.0
                    )

        # Corrupt raw slot with truncated multi-byte UTF-8 sequence must still include 'sym' key cleanly
        corrupt_offset = HEADER_SIZE + ((seq & 63) * SLOT_SIZE)
        struct.pack_into("<Q", writer.shm.buf, corrupt_offset, seq)
        struct.pack_into("<BB", writer.shm.buf, corrupt_offset + 8, 1, 1)  # TICK, VALID
        struct.pack_into("<16s8s", writer.shm.buf, corrupt_offset + 96, b"TRUNC\xe2\x82".ljust(16, b"\x00"), b"BINANCE\x00")
        struct.pack_into("<Q", writer.shm.buf, 24, seq + 1)

        with monkeypatch.context() as mp:
            mp.setattr(shm_mod, "fastpath", None)
            py_corrupt = reader.read_slot(seq)
        assert py_corrupt is not None and "sym" in py_corrupt and isinstance(py_corrupt["sym"], str)
        if fp_mod.has_native_shm():
            c_corrupt = fp_mod.native_shm_read_slot(reader.shm.buf, 64, seq)
            assert c_corrupt is not None and "sym" in c_corrupt and isinstance(c_corrupt["sym"], str)
            assert c_corrupt["sym"] == py_corrupt["sym"]
    finally:
        reader.close()
        writer.close()
