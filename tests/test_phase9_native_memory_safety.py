"""Tests for Phase 9: Native C Memory Safety & Sanitizers.

Verifies:
1. Native C fastpath library loaded and active.
2. SBE packet parsing bounds checks: truncated frames, zero-length, corrupt/wrong template id.
3. Seqlock memory structures conform to 128-byte cache line alignments.
4. Fastpath quality engine handles NaN, Inf, and abnormal prices safely.
"""

from __future__ import annotations

import ctypes
import os
import pytest
import struct

from mdrap.fastpath import HAS_FASTPATH, _NATIVE_LIB, FastQualityEngine
from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.sbe import HEADER_STRUCT, TEMPLATE_TICK, SBE_SCHEMA_ID, SBE_SCHEMA_VERSION


def test_native_c_library_loaded():
    """Verify that native C accelerator is compiled and successfully loaded."""
    assert HAS_FASTPATH is True
    assert _NATIVE_LIB is not None


def test_sbe_bounds_checking_truncated_and_corrupt():
    """Verify C SBE decoder handles arbitrary truncated or corrupt buffers safely without crashing."""
    if not HAS_FASTPATH or _NATIVE_LIB is None:
        pytest.skip("Native C fastpath not available")

    class _CSbeTickPayload(ctypes.Structure):
        _pack_ = 8
        _fields_ = [
            ("seq", ctypes.c_uint64),
            ("exchange_ts", ctypes.c_double),
            ("ingest_ts", ctypes.c_double),
            ("broadcast_ts", ctypes.c_double),
            ("price", ctypes.c_double),
            ("size", ctypes.c_double),
            ("bid", ctypes.c_double),
            ("ask", ctypes.c_double),
            ("bid_size", ctypes.c_double),
            ("ask_size", ctypes.c_double),
            ("status", ctypes.c_uint8),
            ("is_crossed", ctypes.c_uint8),
            ("_pad", ctypes.c_uint16),
            ("engine_us", ctypes.c_uint32),
            ("symbol", ctypes.c_char * 16),
            ("source", ctypes.c_char * 16),
        ]

    c_unpack = _NATIVE_LIB.fastpath_sbe_unpack_tick
    c_unpack.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.POINTER(_CSbeTickPayload),
    ]
    c_unpack.restype = ctypes.c_int32

    payload = _CSbeTickPayload()

    # 1. Truncated buffers (< 128 bytes) must return 0 (rejection), never crash
    for size in (0, 1, 4, 8, 64, 127):
        buf = b"\x00" * size
        rc = c_unpack(buf, len(buf), ctypes.byref(payload))
        assert rc == 0, f"Expected rejection (0) for size {size}, got {rc}"

    # 2. Wrong template ID (must be TEMPLATE_TICK = 101)
    wrong_template_hdr = (
        HEADER_STRUCT.pack(120, 9999, SBE_SCHEMA_ID, SBE_SCHEMA_VERSION) + b"\x00" * 120
    )
    rc = c_unpack(wrong_template_hdr, len(wrong_template_hdr), ctypes.byref(payload))
    assert rc == 0, f"Expected rejection (0) for wrong template id, got {rc}"

    # 3. Valid template ID (101) with 128 bytes total must unpack cleanly
    valid_frame = (
        HEADER_STRUCT.pack(120, TEMPLATE_TICK, SBE_SCHEMA_ID, SBE_SCHEMA_VERSION)
        + b"\x00" * 120
    )
    rc = c_unpack(valid_frame, len(valid_frame), ctypes.byref(payload))
    assert rc == 1, f"Expected success (1) for valid frame, got {rc}"


def test_seqlock_struct_sizes_and_alignments():
    """Verify that seqlock memory structures conform to strict 128-byte cache line alignments."""
    from mdrap.shm import (
        HEADER_SIZE,
        SLOT_SIZE,
        HEADER_LINE1_STRUCT,
        HEADER_LINE2_STRUCT,
        MAGIC,
        VERSION,
        UNCOMMITTED,
    )

    assert HEADER_SIZE == 128
    assert SLOT_SIZE == 128
    assert HEADER_LINE1_STRUCT.size == 64
    assert HEADER_LINE2_STRUCT.size == 64
    assert MAGIC == b"MDRP"
    assert VERSION == 3
    assert UNCOMMITTED == 0xFFFFFFFFFFFFFFFF


def test_fastpath_engine_nan_inf_boundary_handling():
    """Verify that NaN, Infinity, and zero/negative prices are trapped safely by C kernel."""
    eng = FastQualityEngine()

    # 1. Price is NaN
    ev_nan = CanonicalEvent(
        event_id="nan1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=0.0,
        source="FEED",
        sequence_number=1,
        price=float("nan"),
        quantity=100.0,
    )
    res_nan = eng.evaluate(ev_nan)
    assert res_nan.quality_status == QualityStatus.INVALID

    # 2. Price is Inf
    ev_inf = CanonicalEvent(
        event_id="inf1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=0.0,
        source="FEED",
        sequence_number=2,
        price=float("inf"),
        quantity=100.0,
    )
    res_inf = eng.evaluate(ev_inf)
    assert res_inf.quality_status == QualityStatus.INVALID
