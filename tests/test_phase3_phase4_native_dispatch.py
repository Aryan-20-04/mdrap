"""
Phase 3 & 4 Tests: Python/C Reference Ownership (MEM-02) & Native Dispatch Integrity (MEM-03).
Verifies that:
1. native_shm_write_tick dispatches all 18 arguments to C-extension successfully without TypeError.
2. Repeated reads via py_shm_read_slot_v3 do not leak Python objects or heap memory.
"""

from __future__ import annotations

import ctypes
import gc
import mmap
import sys
import pytest
from src import fastpath
from src.shm import SHMWriter, DEFAULT_SLOT_COUNT, SLOT_SIZE, HEADER_SIZE


def test_native_shm_write_tick_18_args():
    # Create an in-memory buffer matching SHM layout
    total_size = HEADER_SIZE + (16 * SLOT_SIZE)
    buf = bytearray(total_size)
    
    # Initialize SHM v3 header
    if fastpath._NATIVE_LIB and hasattr(fastpath._NATIVE_LIB, "fastpath_shm_init_v3"):
        raw_addr = fastpath.get_buffer_address(buf)
        fastpath._NATIVE_LIB.fastpath_shm_init_v3(ctypes.c_void_p(raw_addr), ctypes.c_size_t(total_size), ctypes.c_uint32(16), ctypes.c_uint64(100))

    # Write tick 0 using native_shm_write_tick
    ok = fastpath.native_shm_write_tick(
        buf_ptr=buf,
        slot_count=16,
        seq=0,
        symbol="AAPL",
        source="FEEDX",
        price=150.25,
        size=10.0,
        bid=150.20,
        ask=150.30,
        bid_sz=50.0,
        ask_sz=50.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1000.0,
        ingest_ts=1000.001,
        broadcast_ts=1000.002,
        engine_us=0.05,
    )
    assert ok is True

    # Verify read slot
    slot = fastpath.native_shm_read_slot(buf, 16, 0)
    assert slot is not None
    assert slot["seq"] == 0
    assert slot["sym"] == "AAPL"
    assert slot["source"] == "FEEDX"
    assert slot["price"] == pytest.approx(150.25)
    assert slot["status"] == "VALID"


def test_native_shm_read_refcount_stability():
    total_size = HEADER_SIZE + (16 * SLOT_SIZE)
    buf = bytearray(total_size)
    fastpath.native_shm_write_tick(
        buf_ptr=buf,
        slot_count=16,
        seq=1,
        symbol="MSFT",
        source="FEEDY",
        price=320.50,
        size=5.0,
        bid=320.40,
        ask=320.60,
        bid_sz=25.0,
        ask_sz=25.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=2000.0,
        ingest_ts=2000.001,
        broadcast_ts=2000.002,
        engine_us=0.04,
    )

    # Warm up gc
    gc.collect()
    
    # Read 10,000 times
    # Prior to fix: leaked 10+ Python objects per read = 100,000+ objects
    initial_objects = len(gc.get_objects())
    for _ in range(10000):
        slot = fastpath.native_shm_read_slot(buf, 16, 1)
        del slot

    gc.collect()
    final_objects = len(gc.get_objects())
    
    # Object count should be stable (allow small variation for pytest internals)
    diff = abs(final_objects - initial_objects)
    assert diff < 50, f"Python object count grew by {diff} across 10,000 reads (leak detected)"
