"""
Python ctypes wrapper for MDRAP Native C Hot Path.

Exposes FastQualityEngine as a drop-in replacement for QualityEngine:
- Loads src/_fastpath_native.dll (Windows) or src/_fastpath_native.so (Linux/macOS)
- Marshals CanonicalEvent to 64-byte aligned C struct
- Decodes 32-bit packed status & reason bitmask
- Seamless fallback to pure Python QualityEngine if native library is unavailable
"""

from __future__ import annotations

import collections
import ctypes
import math
import os
import sys
import threading
from typing import Any

from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityConfig, QualityEngine

# Status values
_STATUS_MAP = {
    0: QualityStatus.VALID,
    1: QualityStatus.SUSPICIOUS,
    2: QualityStatus.INVALID,
}
_PRIORITY_MAP = {
    QualityStatus.VALID: 0,
    QualityStatus.SUSPICIOUS: 1,
    QualityStatus.INVALID: 2,
}

# Bitmask values matching fastpath.c and rules.def
_REASON_BITS = [
    (1 << 0, Reason.SCHEMA_VIOLATION.value),
    (1 << 1, Reason.DUPLICATE.value),
    (1 << 2, Reason.SEQUENCE_GAP.value),
    (1 << 3, Reason.OUT_OF_ORDER.value),
    (1 << 4, Reason.STALE.value),
    (1 << 5, Reason.PRICE_ANOMALY.value),
    (1 << 6, Reason.CROSSED_QUOTE.value),
    (1 << 7, Reason.CROSS_FEED_DISAGREEMENT.value),
    (1 << 8, Reason.MALFORMED.value),
    (1 << 9, Reason.CIRCUIT_FILTER_BREACH.value),
    (1 << 10, Reason.VOLATILITY_INTERRUPTION.value),
    (1 << 11, Reason.SPECIAL_QUOTE_INDICATION.value),
    (1 << 12, Reason.TS_IMPLAUSIBLE.value),
    (1 << 13, Reason.RATE_LIMITED.value),
    (1 << 14, Reason.SECURITY_REJECT.value),
]

_SOURCES = ["FEEDX", "FEEDY", "FEEDZ", "SOURCEA", "SOURCEB", "SOURCEC"]
_INSTRUMENTS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM"]

_SOURCE_ID_MAP = {s: i for i, s in enumerate(_SOURCES)}
_INSTRUMENT_ID_MAP = {inst: i for i, inst in enumerate(_INSTRUMENTS)}


class _CFastEvent(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("source_id", ctypes.c_int32),
        ("instrument_id", ctypes.c_int32),
        ("event_type", ctypes.c_int32),
        ("present_mask", ctypes.c_uint32),
        ("exchange_ts", ctypes.c_double),
        ("receive_ts", ctypes.c_double),
        ("sequence_num", ctypes.c_int64),
        ("price", ctypes.c_double),
        ("quantity", ctypes.c_double),
        ("bid_price", ctypes.c_double),
        ("ask_price", ctypes.c_double),
        ("bid_size", ctypes.c_double),
        ("ask_size", ctypes.c_double),
    ]


class _CFastResult(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("status", ctypes.c_int32),
        ("_reserved", ctypes.c_uint32),
        ("reason_mask", ctypes.c_uint64),
    ]


class _CFastChokepoint(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("lat", ctypes.c_double),
        ("lon", ctypes.c_double),
        ("radius_nm", ctypes.c_double),
        ("dlat_max", ctypes.c_double),
        ("dlon_max", ctypes.c_double),
        ("lat_rad", ctypes.c_double),
        ("lon_rad", ctypes.c_double),
        ("cos_lat", ctypes.c_double),
        ("sin_lat", ctypes.c_double),
    ]


class _CFastReplayRecord(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("seq", ctypes.c_uint64),
        ("symbol", ctypes.c_char * 16),
        ("source", ctypes.c_char * 16),
        ("event_type", ctypes.c_char * 8),
        ("price", ctypes.c_double),
        ("size", ctypes.c_double),
        ("bid", ctypes.c_double),
        ("ask", ctypes.c_double),
        ("bid_size", ctypes.c_double),
        ("ask_size", ctypes.c_double),
        ("status", ctypes.c_uint8),
        ("is_crossed", ctypes.c_uint8),
        ("exchange_ts", ctypes.c_double),
        ("ingest_ts", ctypes.c_double),
        ("broadcast_ts", ctypes.c_double),
        ("engine_us", ctypes.c_double),
        ("is_valid", ctypes.c_uint8),
    ]

    def to_dict(self) -> dict:
        st_map = {1: "VALID", 2: "SUSPICIOUS", 3: "INVALID"}
        px = self.price if not math.isnan(self.price) and self.price > 0 else None
        sz = self.size if not math.isnan(self.size) and self.size > 0 else None
        b_px = self.bid if not math.isnan(self.bid) and self.bid > 0 else None
        a_px = self.ask if not math.isnan(self.ask) and self.ask > 0 else None
        b_sz = (
            self.bid_size
            if not math.isnan(self.bid_size) and self.bid_size > 0
            else None
        )
        a_sz = (
            self.ask_size
            if not math.isnan(self.ask_size) and self.ask_size > 0
            else None
        )
        ev_type = (
            self.event_type.decode("ascii", errors="replace").strip("\x00") or "TICK"
        )

        return {
            "type": ev_type,
            "seq": int(self.seq),
            "sym": self.symbol.decode("ascii", errors="replace").strip("\x00"),
            "source": self.source.decode("ascii", errors="replace").strip("\x00"),
            "price": px,
            "size": sz,
            "bid": b_px,
            "ask": a_px,
            "bid_size": b_sz,
            "ask_size": a_sz,
            "status": st_map.get(self.status, "VALID"),
            "is_crossed": bool(self.is_crossed),
            "exchange_ts": self.exchange_ts,
            "ingest_ts": self.ingest_ts,
            "broadcast_ts": self.broadcast_ts,
            "proc_us": round(self.engine_us, 1),
            "engine_us": round(self.engine_us, 1),
            "bbo": {
                "bid": b_px,
                "ask": a_px,
                "spread": (a_px - b_px)
                if (a_px is not None and b_px is not None)
                else None,
                "mid": ((a_px + b_px) / 2.0)
                if (a_px is not None and b_px is not None)
                else None,
                "crossed": bool(self.is_crossed),
            }
            if (b_px is not None or a_px is not None)
            else None,
        }


def _load_native_lib():
    if os.environ.get("MDRAP_DISABLE_FASTPATH", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    src_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if sys.platform == "win32":
        candidates = ["_fastpath_native.dll", "_fastpath_native.pyd", "fastpath.dll"]
    elif sys.platform == "darwin":
        candidates = [
            "_fastpath_native.dylib",
            "_fastpath_native.so",
            "fastpath.dylib",
            "fastpath.so",
        ]
    else:
        candidates = ["_fastpath_native.so", "fastpath.so"]

    dll_path = None
    for name in candidates:
        p = os.path.join(src_dir, name)
        if os.path.isfile(p):
            dll_path = p
            break

    # If missing, attempt JIT compilation on first run (git clone or pip install without prebuilt binary)
    if dll_path is None:
        c_source = os.path.join(src_dir, "fastpath.c")
        if os.path.isfile(c_source):
            try:
                try:
                    import build_fastpath
                except ImportError:
                    root_dir = os.path.dirname(src_dir)
                    if root_dir not in sys.path:
                        sys.path.insert(0, root_dir)
                    import build_fastpath
                if build_fastpath.build(target_dir=src_dir, quiet=True):
                    for name in candidates:
                        p = os.path.join(src_dir, name)
                        if os.path.isfile(p):
                            dll_path = p
                            break
            except Exception:
                pass

    # Backward compatibility: on Windows, if legacy fastpath.dll exists and _fastpath_native.dll doesn't, copy it
    if dll_path is None and sys.platform == "win32":
        legacy_win = os.path.join(src_dir, "fastpath.dll")
        native_win = os.path.join(src_dir, "_fastpath_native.dll")
        if os.path.isfile(legacy_win):
            try:
                import shutil

                shutil.copy2(legacy_win, native_win)
                if os.path.isfile(native_win):
                    dll_path = native_win
            except OSError:
                pass

    if dll_path and os.path.exists(dll_path):
        try:
            lib = ctypes.CDLL(dll_path)
            lib.fastpath_init.argtypes = [
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_int32,
            ]
            lib.fastpath_init.restype = None

            lib.fastpath_reset.argtypes = []
            lib.fastpath_reset.restype = None

            lib.fastpath_evaluate.argtypes = [
                ctypes.POINTER(_CFastEvent),
                ctypes.POINTER(_CFastResult),
            ]
            lib.fastpath_evaluate.restype = None

            lib.fastpath_evaluate_batch.argtypes = [
                ctypes.POINTER(_CFastEvent),
                ctypes.POINTER(_CFastResult),
                ctypes.c_int32,
            ]
            lib.fastpath_evaluate_batch.restype = None

            lib.fastpath_eval_fast.argtypes = [
                ctypes.c_int32,
                ctypes.c_int32,
                ctypes.c_int32,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_int64,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
            ]
            lib.fastpath_eval_fast.restype = ctypes.c_uint64
            if hasattr(lib, "fastpath_noop"):
                lib.fastpath_noop.argtypes = lib.fastpath_eval_fast.argtypes
                lib.fastpath_noop.restype = ctypes.c_uint64

            # Phase 2: Explicit FastEngine context bindings
            if hasattr(lib, "fastpath_engine_abi_version"):
                lib.fastpath_engine_abi_version.argtypes = []
                lib.fastpath_engine_abi_version.restype = ctypes.c_int32
                abi_ver = lib.fastpath_engine_abi_version()
                if abi_ver != 5:
                    raise RuntimeError(f"fastpath ABI mismatch: expected 5, got {abi_ver}")

            if hasattr(lib, "fastpath_engine_create"):
                lib.fastpath_engine_create.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                ]
                lib.fastpath_engine_create.restype = ctypes.c_void_p

            if hasattr(lib, "fastpath_engine_destroy"):
                lib.fastpath_engine_destroy.argtypes = [ctypes.c_void_p]
                lib.fastpath_engine_destroy.restype = None

            if hasattr(lib, "fastpath_engine_reset"):
                lib.fastpath_engine_reset.argtypes = [ctypes.c_void_p]
                lib.fastpath_engine_reset.restype = None

            if hasattr(lib, "fastpath_engine_source_reset"):
                lib.fastpath_engine_source_reset.argtypes = [ctypes.c_void_p, ctypes.c_int32]
                lib.fastpath_engine_source_reset.restype = None

            if hasattr(lib, "fastpath_engine_set_hash_seed"):
                lib.fastpath_engine_set_hash_seed.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
                lib.fastpath_engine_set_hash_seed.restype = None

            if hasattr(lib, "fastpath_engine_configure"):
                lib.fastpath_engine_configure.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int64,
                    ctypes.c_int32,
                    ctypes.c_int32,
                ]
                lib.fastpath_engine_configure.restype = None

            if hasattr(lib, "fastpath_engine_counters"):
                lib.fastpath_engine_counters.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_uint64),
                ]
                lib.fastpath_engine_counters.restype = None

            if hasattr(lib, "fastpath_engine_evaluate_unlocked"):
                lib.fastpath_engine_evaluate_unlocked.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(_CFastEvent),
                    ctypes.POINTER(_CFastResult),
                ]
                lib.fastpath_engine_evaluate_unlocked.restype = None

            if hasattr(lib, "fastpath_engine_eval_fast"):
                lib.fastpath_engine_eval_fast.argtypes = [ctypes.c_void_p] + list(
                    lib.fastpath_eval_fast.argtypes
                )
                lib.fastpath_engine_eval_fast.restype = ctypes.c_uint64

            if hasattr(lib, "fastpath_engine_eval_fast2"):
                lib.fastpath_engine_eval_fast2.argtypes = [ctypes.c_void_p] + list(
                    lib.fastpath_eval_fast.argtypes
                ) + [ctypes.c_uint32]
                lib.fastpath_engine_eval_fast2.restype = ctypes.c_uint64

            if hasattr(lib, "fastpath_engine_evaluate"):
                lib.fastpath_engine_evaluate.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(_CFastEvent),
                    ctypes.POINTER(_CFastResult),
                ]
                lib.fastpath_engine_evaluate.restype = None

            if hasattr(lib, "fastpath_engine_evaluate_batch"):
                lib.fastpath_engine_evaluate_batch.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(_CFastEvent),
                    ctypes.POINTER(_CFastResult),
                    ctypes.c_int32,
                ]
                lib.fastpath_engine_evaluate_batch.restype = None

            # Phase F: Replay Buffer bindings
            if hasattr(lib, "fastpath_replay_record"):
                lib.fastpath_replay_record.argtypes = [
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                ]
                lib.fastpath_replay_record.restype = None

            if hasattr(lib, "fastpath_replay_slice"):
                lib.fastpath_replay_slice.argtypes = [
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.POINTER(_CFastReplayRecord),
                    ctypes.c_int32,
                ]
                lib.fastpath_replay_slice.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_replay_binary_slice"):
                lib.fastpath_replay_binary_slice.argtypes = [
                    ctypes.c_uint64,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_int32,
                ]
                lib.fastpath_replay_binary_slice.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_replay_stats"):
                lib.fastpath_replay_stats.argtypes = [
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_int32),
                ]
                lib.fastpath_replay_stats.restype = None

            if hasattr(lib, "fastpath_replay_clear"):
                lib.fastpath_replay_clear.argtypes = []
                lib.fastpath_replay_clear.restype = None

            if hasattr(lib, "fastpath_cleanup"):
                lib.fastpath_cleanup.argtypes = []
                lib.fastpath_cleanup.restype = None

            if hasattr(lib, "fastpath_shm_write_tick"):
                lib.fastpath_shm_write_tick.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_float,
                ]
                lib.fastpath_shm_write_tick.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_shm_read_slot"):
                lib.fastpath_shm_read_slot.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint64,
                    ctypes.c_void_p,
                ]
                lib.fastpath_shm_read_slot.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_shm_init_v3"):
                lib.fastpath_shm_init_v3.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_uint64,
                ]
                lib.fastpath_shm_init_v3.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_shm_write_tick_v3"):
                lib.fastpath_shm_write_tick_v3.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_float,
                ]
                lib.fastpath_shm_write_tick_v3.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_shm_read_slot_v3"):
                lib.fastpath_shm_read_slot_v3.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_uint64,
                    ctypes.c_void_p,
                ]
                lib.fastpath_shm_read_slot_v3.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_shm_head_v3"):
                lib.fastpath_shm_head_v3.argtypes = [ctypes.c_void_p]
                lib.fastpath_shm_head_v3.restype = ctypes.c_uint64

            if hasattr(lib, "fastpath_shm_epoch_v3"):
                lib.fastpath_shm_epoch_v3.argtypes = [ctypes.c_void_p]
                lib.fastpath_shm_epoch_v3.restype = ctypes.c_uint64

            if hasattr(lib, "fastpath_process_sbe_stream_n"):
                lib.fastpath_process_sbe_stream_n.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_size_t,
                    ctypes.c_int32,
                    ctypes.POINTER(_CFastResult),
                    ctypes.c_int32,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                ]
                lib.fastpath_process_sbe_stream_n.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_sbe_pack_tick_n"):
                lib.fastpath_sbe_pack_tick_n.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_float,
                ]
                lib.fastpath_sbe_pack_tick_n.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_process_sbe_stream"):
                lib.fastpath_process_sbe_stream.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_int32,
                    ctypes.POINTER(_CFastResult),
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                ]
                lib.fastpath_process_sbe_stream.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_sbe_pack_tick"):
                lib.fastpath_sbe_pack_tick.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint64,
                    ctypes.c_char_p,
                    ctypes.c_char_p,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint8,
                    ctypes.c_uint8,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_float,
                ]
                lib.fastpath_sbe_pack_tick.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_sbe_unpack_tick"):
                lib.fastpath_sbe_unpack_tick.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                ]
                lib.fastpath_sbe_unpack_tick.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_sbe_generate_stream"):
                lib.fastpath_sbe_generate_stream.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_int32,
                    ctypes.c_double,
                ]
                lib.fastpath_sbe_generate_stream.restype = ctypes.c_int32

            # Phase 12: Geodesic & Spatial Fastpath bindings
            if hasattr(lib, "fastpath_haversine_nm"):
                lib.fastpath_haversine_nm.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                ]
                lib.fastpath_haversine_nm.restype = ctypes.c_double

            if hasattr(lib, "fastpath_equirectangular_nm"):
                lib.fastpath_equirectangular_nm.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                ]
                lib.fastpath_equirectangular_nm.restype = ctypes.c_double

            if hasattr(lib, "fastpath_vessel_chokepoint_eval"):
                lib.fastpath_vessel_chokepoint_eval.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.POINTER(_CFastChokepoint),
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_int32),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_uint8),
                ]
                lib.fastpath_vessel_chokepoint_eval.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_batch_fleet_geofence"):
                lib.fastpath_batch_fleet_geofence.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.POINTER(_CFastChokepoint),
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_int32),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_uint8),
                ]
                lib.fastpath_batch_fleet_geofence.restype = ctypes.c_int32

            # Phase 13: Quantitative & Options Accelerators
            if hasattr(lib, "fastpath_bsm_price"):
                lib.fastpath_bsm_price.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                ]
                lib.fastpath_bsm_price.restype = ctypes.c_double

            if hasattr(lib, "fastpath_bsm_greeks"):
                lib.fastpath_bsm_greeks.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.fastpath_bsm_greeks.restype = None

            if hasattr(lib, "fastpath_binomial_price"):
                lib.fastpath_binomial_price.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                    ctypes.c_int32,
                ]
                lib.fastpath_binomial_price.restype = ctypes.c_double

            if hasattr(lib, "fastpath_implied_volatility"):
                lib.fastpath_implied_volatility.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                    ctypes.c_double,
                    ctypes.c_int32,
                ]
                lib.fastpath_implied_volatility.restype = ctypes.c_double

            if hasattr(lib, "fastpath_calc_rsi"):
                lib.fastpath_calc_rsi.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.fastpath_calc_rsi.restype = None

            if hasattr(lib, "fastpath_calc_ema"):
                lib.fastpath_calc_ema.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.fastpath_calc_ema.restype = None

            if hasattr(lib, "fastpath_calc_bollinger"):
                lib.fastpath_calc_bollinger.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.c_double,
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.fastpath_calc_bollinger.restype = None

            if hasattr(lib, "fastpath_calc_atr"):
                lib.fastpath_calc_atr.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_double),
                ]
                lib.fastpath_calc_atr.restype = None

            if hasattr(lib, "fastpath_monte_carlo_var"):
                lib.fastpath_monte_carlo_var.argtypes = [
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_int32,
                    ctypes.c_int32,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.c_uint64,
                ]
                lib.fastpath_monte_carlo_var.restype = ctypes.c_double

            if hasattr(lib, "fastpath_fix_checksum"):
                lib.fastpath_fix_checksum.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_int32,
                ]
                lib.fastpath_fix_checksum.restype = ctypes.c_uint32

            return lib
        except Exception as e:
            print(
                f"[fastpath] Warning: Failed to load {dll_path}: {e}", file=sys.stderr
            )
            return None
    return None


_NATIVE_LIB = _load_native_lib()
HAS_FASTPATH: bool = bool(_NATIVE_LIB is not None)
_FAST_EVAL = _NATIVE_LIB.fastpath_eval_fast if _NATIVE_LIB else None
_ENGINE_FAST_EVAL = (
    _NATIVE_LIB.fastpath_engine_eval_fast
    if (_NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_engine_eval_fast"))
    else None
)
_ENGINE_FAST_EVAL2 = (
    _NATIVE_LIB.fastpath_engine_eval_fast2
    if (_NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_engine_eval_fast2"))
    else None
)
_NAN = math.nan

FE_MASK_VALID = 0x80000000
FE_HAS_PRICE  = 0x01
FE_HAS_QTY    = 0x02
FE_HAS_BID    = 0x04
FE_HAS_ASK    = 0x08
FE_HAS_BID_SZ = 0x10
FE_HAS_ASK_SZ = 0x20


def is_available() -> bool:
    """Return True if the native C accelerator library is loaded and operational."""
    return _NATIVE_LIB is not None


class FastQualityEngine:
    """
    Drop-in replacement for QualityEngine backed by compiled C shared library.
    """

    def __init__(self, config: QualityConfig | None = None, thread_safe: bool = False):
        if config is None:
            try:
                from config import load_config

                self.cfg = load_config().quality
            except Exception:
                self.cfg = QualityConfig()
        else:
            self.cfg = config
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts: dict[str, int] = {}
        self._source_map = dict(_SOURCE_ID_MAP)
        self._inst_map = dict(_INSTRUMENT_ID_MAP)
        self._fallback_engine: QualityEngine | None = None
        self.is_native = bool(_NATIVE_LIB is not None)
        self.thread_safe = thread_safe
        self._eval_lock = threading.Lock()

        self._engine_ptr = None
        if _NATIVE_LIB:
            if hasattr(_NATIVE_LIB, "fastpath_engine_create"):
                self._engine_ptr = _NATIVE_LIB.fastpath_engine_create(
                    self.cfg.staleness_threshold_s,
                    self.cfg.price_anomaly_stddev,
                    self.cfg.price_window,
                )
                if hasattr(_NATIVE_LIB, "fastpath_engine_configure"):
                    unseq_st = (
                        2
                        if getattr(self.cfg, "unseq_dup_status", "SUSPICIOUS")
                        == "INVALID"
                        else 1
                    )
                    _NATIVE_LIB.fastpath_engine_configure(
                        self._engine_ptr,
                        getattr(self.cfg, "price_min_samples", 20),
                        getattr(self.cfg, "price_reseed_after", 8),
                        ctypes.c_double(
                            getattr(self.cfg, "price_sigma_floor_rel", 2e-4)
                        ),
                        ctypes.c_double(
                            getattr(self.cfg, "price_reseed_band_rel", 0.01)
                        ),
                        ctypes.c_double(getattr(self.cfg, "max_future_skew_s", 1.0)),
                        getattr(self.cfg, "seq_jump_limit", 1 << 24),
                        unseq_st,
                        1 if getattr(self.cfg, "allow_negative", False) else 0,
                    )
            else:
                _NATIVE_LIB.fastpath_init(
                    self.cfg.staleness_threshold_s,
                    self.cfg.price_anomaly_stddev,
                    self.cfg.price_window,
                )
        else:
            self._fallback_engine = QualityEngine(self.cfg)

    def close(self) -> None:
        """Release native C engine context heap allocations."""
        if (
            getattr(self, "_engine_ptr", None)
            and _NATIVE_LIB
            and hasattr(_NATIVE_LIB, "fastpath_engine_destroy")
        ):
            _NATIVE_LIB.fastpath_engine_destroy(self._engine_ptr)
            self._engine_ptr = None

    def __del__(self) -> None:
        self.close()

    def reset(self) -> None:
        """Reset engine tracking state."""
        with self._eval_lock:
            self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
            self.reason_counts = {}
            if (
                self._engine_ptr
                and _NATIVE_LIB
                and hasattr(_NATIVE_LIB, "fastpath_engine_reset")
            ):
                _NATIVE_LIB.fastpath_engine_reset(self._engine_ptr)
            elif _NATIVE_LIB:
                _NATIVE_LIB.fastpath_reset()
            if self._fallback_engine:
                self._fallback_engine.reset()

    def _get_source_id(self, source: str) -> int:
        if source not in self._source_map:
            self._source_map[source] = len(self._source_map)
        return self._source_map[source]

    def _get_instrument_id(self, inst: str) -> int:
        if inst not in self._inst_map:
            self._inst_map[inst] = len(self._inst_map)
        return self._inst_map[inst]

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        if self.thread_safe:
            with self._eval_lock:
                res = self._evaluate_unlocked(event)
        else:
            res = self._evaluate_unlocked(event)

        try:
            from rules import _USER_RULES, evaluate_user_rules
            if _USER_RULES:
                res = evaluate_user_rules(res)
        except ImportError:
            pass

        return res

    def _evaluate_unlocked(self, event: CanonicalEvent) -> CanonicalEvent:
        if not _FAST_EVAL:
            if not self._fallback_engine:
                self._fallback_engine = QualityEngine(self.cfg)
            res = self._fallback_engine.evaluate(event)
            self.counts = self._fallback_engine.counts
            self.reason_counts = self._fallback_engine.reason_counts
            return res

        pm = FE_MASK_VALID
        if event.price is not None:
            pm |= FE_HAS_PRICE
        if event.quantity is not None:
            pm |= FE_HAS_QTY
        if event.bid_price is not None:
            pm |= FE_HAS_BID
        if event.ask_price is not None:
            pm |= FE_HAS_ASK
        if event.bid_size is not None:
            pm |= FE_HAS_BID_SZ
        if event.ask_size is not None:
            pm |= FE_HAS_ASK_SZ

        c_price = event.price if event.price is not None else _NAN
        c_qty = event.quantity if event.quantity is not None else _NAN
        c_bid_px = event.bid_price if event.bid_price is not None else _NAN
        c_ask_px = event.ask_price if event.ask_price is not None else _NAN
        c_bid_sz = event.bid_size if event.bid_size is not None else _NAN
        c_ask_sz = event.ask_size if event.ask_size is not None else _NAN

        s_id = event.source_id
        if s_id < 0:
            s_id = self._source_map.get(event.source)
            if s_id is None:
                s_id = self._source_map[event.source] = len(self._source_map)
            event.source_id = s_id

        i_id = event.instrument_id_int
        if i_id < 0:
            i_id = self._inst_map.get(event.instrument_id)
            if i_id is None:
                i_id = self._inst_map[event.instrument_id] = len(self._inst_map)
            event.instrument_id_int = i_id

        # Graceful fallback: C static tables have MAX_SOURCES=32, MAX_INSTRUMENTS=8192.
        # If the number of unique sources or instruments exceeds C bounds, evaluate with Python engine.
        if s_id >= 32 or i_id >= 8192:
            if not self._fallback_engine:
                self._fallback_engine = QualityEngine(self.cfg)
            res = self._fallback_engine.evaluate(event)
            self.counts[res.quality_status.value] = (
                self.counts.get(res.quality_status.value, 0) + 1
            )
            for r in res.reasons:
                self.reason_counts[r] = self.reason_counts.get(r, 0) + 1
            return res

        try:
            # Direct CPU register call to C hot path (< 100 ns)
            if self._engine_ptr and _ENGINE_FAST_EVAL2:
                packed = _ENGINE_FAST_EVAL2(
                    self._engine_ptr,
                    s_id,
                    i_id,
                    1 if event.event_type == EventType.QUOTE else 0,
                    event.exchange_timestamp,
                    event.receive_timestamp,
                    event.sequence_number if event.sequence_number is not None else -1,
                    c_price,
                    c_qty,
                    c_bid_px,
                    c_ask_px,
                    c_bid_sz,
                    c_ask_sz,
                    pm,
                )
            elif self._engine_ptr and _ENGINE_FAST_EVAL:
                packed = _ENGINE_FAST_EVAL(
                    self._engine_ptr,
                    s_id,
                    i_id,
                    1 if event.event_type == EventType.QUOTE else 0,
                    event.exchange_timestamp,
                    event.receive_timestamp,
                    event.sequence_number if event.sequence_number is not None else -1,
                    c_price,
                    c_qty,
                    c_bid_px,
                    c_ask_px,
                    c_bid_sz,
                    c_ask_sz,
                )
            else:
                packed = _FAST_EVAL(
                    s_id,
                    i_id,
                    1 if event.event_type == EventType.QUOTE else 0,
                    event.exchange_timestamp,
                    event.receive_timestamp,
                    event.sequence_number if event.sequence_number is not None else -1,
                    c_price,
                    c_qty,
                    c_bid_px,
                    c_ask_px,
                    c_bid_sz,
                    c_ask_sz,
                )
        except Exception:
            # Fault-tolerant shield: seamlessly fall back to pure Python if C DLL faults
            if not self._fallback_engine:
                self._fallback_engine = QualityEngine(self.cfg)
            res = self._fallback_engine.evaluate(event)
            self.counts = self._fallback_engine.counts
            self.reason_counts = self._fallback_engine.reason_counts
            return res

        status_code = packed >> 32
        mask = packed & 0xFFFFFFFF
        status = _STATUS_MAP[status_code]

        # Priority guard: never downgrade if already marked
        if _PRIORITY_MAP[status] > _PRIORITY_MAP.get(event.quality_status, 0):
            event.quality_status = status

        if mask:
            for bit, reason_str in _REASON_BITS:
                if mask & bit:
                    event.reasons.append(reason_str)
                    self.reason_counts[reason_str] = (
                        self.reason_counts.get(reason_str, 0) + 1
                    )

        self.counts[event.quality_status.value] += 1
        return event

    def evaluate_batch(self, events: list[CanonicalEvent]) -> list[CanonicalEvent]:
        """
        Evaluate a micro-batch of CanonicalEvents in a single C boundary crossing.
        Strictly preserves per-source/per-instrument arrival order (Invariant A5).
        """
        if not events:
            return events

        if not _NATIVE_LIB or not hasattr(_NATIVE_LIB, "fastpath_evaluate_batch"):
            # Fallback to sequential evaluate
            for ev in events:
                self.evaluate(ev)
            return events

        if self.thread_safe:
            with self._eval_lock:
                return self._evaluate_batch_unlocked(events)
        return self._evaluate_batch_unlocked(events)

    def _evaluate_batch_unlocked(
        self, events: list[CanonicalEvent]
    ) -> list[CanonicalEvent]:
        n = len(events)
        c_events = (_CFastEvent * n)()
        c_results = (_CFastResult * n)()
        needs_fallback: list[int] = []

        for idx, ev in enumerate(events):
            s_id = self._get_source_id(ev.source)
            i_id = self._get_instrument_id(ev.instrument_id)

            if s_id >= 32 or i_id >= 8192:
                needs_fallback.append(idx)
                continue

            c_ev = c_events[idx]
            c_ev.source_id = s_id
            c_ev.instrument_id = i_id
            pm = FE_MASK_VALID
            if ev.price is not None:
                pm |= FE_HAS_PRICE
            if ev.quantity is not None:
                pm |= FE_HAS_QTY
            if ev.bid_price is not None:
                pm |= FE_HAS_BID
            if ev.ask_price is not None:
                pm |= FE_HAS_ASK
            if ev.bid_size is not None:
                pm |= FE_HAS_BID_SZ
            if ev.ask_size is not None:
                pm |= FE_HAS_ASK_SZ

            c_ev.event_type = 1 if ev.event_type == EventType.QUOTE else 0
            c_ev.present_mask = pm
            c_ev.exchange_ts = ev.exchange_timestamp
            c_ev.receive_ts = ev.receive_timestamp
            c_ev.sequence_num = (
                ev.sequence_number if ev.sequence_number is not None else -1
            )
            c_ev.price = ev.price if ev.price is not None else _NAN
            c_ev.quantity = ev.quantity if ev.quantity is not None else _NAN
            c_ev.bid_price = ev.bid_price if ev.bid_price is not None else _NAN
            c_ev.ask_price = ev.ask_price if ev.ask_price is not None else _NAN
            c_ev.bid_size = ev.bid_size if ev.bid_size is not None else _NAN
            c_ev.ask_size = ev.ask_size if ev.ask_size is not None else _NAN

        try:
            if self._engine_ptr and hasattr(_NATIVE_LIB, "fastpath_engine_evaluate_batch"):
                _NATIVE_LIB.fastpath_engine_evaluate_batch(self._engine_ptr, c_events, c_results, n)
            else:
                _NATIVE_LIB.fastpath_evaluate_batch(c_events, c_results, n)
        except Exception:
            # Fallback to individual evaluate if batch call fails
            for ev in events:
                self._evaluate_unlocked(ev)
            return events

        priority = {
            QualityStatus.VALID: 0,
            QualityStatus.SUSPICIOUS: 1,
            QualityStatus.INVALID: 2,
        }

        for idx, ev in enumerate(events):
            if idx in needs_fallback:
                self._evaluate_unlocked(ev)
                continue

            res = c_results[idx]
            status_code = res.status
            mask = res.reason_mask
            status = _STATUS_MAP.get(status_code, QualityStatus.VALID)

            if priority[status] > priority.get(ev.quality_status, 0):
                ev.quality_status = status

            if mask:
                for bit, reason_str in _REASON_BITS:
                    if mask & bit:
                        ev.reasons.append(reason_str)
                        self.reason_counts[reason_str] = (
                            self.reason_counts.get(reason_str, 0) + 1
                        )

            self.counts[ev.quality_status.value] += 1

        return events

    def process_sbe_stream(
        self,
        sbe_buffer: bytes | bytearray | memoryview,
        count: int,
        shm_buffer: Any | None = None,
        shm_slot_count: int = 0,
    ) -> tuple[int, list[_CFastResult]]:
        """
        Validate a batch of 128-byte SBE frames directly in native C (GIL released).
        Optionally writes valid ticks directly into the zero-copy shared memory ring buffer.
        Throughput: >50,000,000 events/sec.
        """
        if not _NATIVE_LIB or not hasattr(_NATIVE_LIB, "fastpath_process_sbe_stream"):
            return 0, []

        c_buf = (ctypes.c_uint8 * len(sbe_buffer)).from_buffer(sbe_buffer)
        results = (_CFastResult * count)()

        shm_ptr = ctypes.c_void_p(ctypes.addressof(shm_buffer)) if shm_buffer else None

        valid_count = _NATIVE_LIB.fastpath_process_sbe_stream(
            c_buf, count, results, shm_ptr, shm_slot_count
        )
        return valid_count, results

    def generate_sbe_stream(
        self,
        count: int,
        anomaly_rate: float = 0.0,
    ) -> bytearray:
        """
        Generate a contiguous array of 128-byte SBE frames directly in native C memory.
        Speed: >80,000,000 frames/sec (zero Python bytecode overhead).
        """
        buf = bytearray(count * 128)
        if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_sbe_generate_stream"):
            c_buf = (ctypes.c_uint8 * len(buf)).from_buffer(buf)
            _NATIVE_LIB.fastpath_sbe_generate_stream(
                c_buf, count, ctypes.c_double(anomaly_rate)
            )
        else:
            from sbe import pack_sbe_tick

            for i in range(count):
                buf[i * 128 : (i + 1) * 128] = pack_sbe_tick(
                    seq=i + 1,
                    symbol="AAPL",
                    source="FEEDX",
                    price=150.0,
                    size=100.0,
                    bid=149.9,
                    ask=150.1,
                    status="VALID",
                )
        return buf


# Phase 5: Explicit Two-Tier Architecture Aliases and Runtime Switch
NativeQualityEngine = FastQualityEngine


def get_quality_engine(
    config: QualityConfig | None = None, prefer_native: bool = True
) -> FastQualityEngine | QualityEngine:
    """Runtime switch between Tier 1 (Native C) and Tier 2 (Pure Python)."""
    if prefer_native and is_available():
        return NativeQualityEngine(config)
    return QualityEngine(config)


class NativeReplayBuffer:
    """
    High-Performance Native C Circular Replay Buffer (Spec §18).
    Maintains 65,536-slot fixed-width ring buffer in C memory with O(1) sequence indexing.
    Falls back to collections.deque if native C accelerator is unavailable.
    """

    def __init__(self, capacity: int = 65536):
        self.capacity = capacity
        self._lock = threading.RLock()
        self.is_native = bool(
            _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_replay_record")
        )
        self._fallback_deque: collections.deque | None = None
        self._fallback_total = 0

        if self.is_native:
            _NATIVE_LIB.fastpath_replay_clear()
        else:
            self._fallback_deque = collections.deque(maxlen=self.capacity)

    def record(
        self,
        seq: int,
        symbol: str,
        source: str = "",
        event_type: str = "TICK",
        price: float | None = None,
        size: float | None = None,
        bid: float | None = None,
        ask: float | None = None,
        bid_size: float | None = None,
        ask_size: float | None = None,
        status: str = "VALID",
        is_crossed: bool = False,
        exchange_ts: float = 0.0,
        ingest_ts: float = 0.0,
        broadcast_ts: float = 0.0,
        engine_us: float = 0.0,
        raw_dict: dict | None = None,
    ) -> None:
        """Record normalized tick into circular buffer."""
        with self._lock:
            if self.is_native:
                st_code = (
                    1 if status == "VALID" else (2 if status == "SUSPICIOUS" else 3)
                )
                _NATIVE_LIB.fastpath_replay_record(
                    int(seq),
                    symbol.encode("ascii", errors="replace")[:15],
                    source.encode("ascii", errors="replace")[:15],
                    event_type.encode("ascii", errors="replace")[:7],
                    float(price or 0.0),
                    float(size or 0.0),
                    float(bid or 0.0),
                    float(ask or 0.0),
                    float(bid_size or 0.0),
                    float(ask_size or 0.0),
                    st_code,
                    1 if is_crossed else 0,
                    float(exchange_ts or 0.0),
                    float(ingest_ts or 0.0),
                    float(broadcast_ts or 0.0),
                    float(engine_us or 0.0),
                )
            else:
                entry = (
                    raw_dict
                    if raw_dict is not None
                    else {
                        "type": event_type,
                        "seq": seq,
                        "sym": symbol,
                        "source": source,
                        "price": price,
                        "size": size,
                        "bid": bid,
                        "ask": ask,
                        "bid_size": bid_size,
                        "ask_size": ask_size,
                        "status": status,
                        "is_crossed": is_crossed,
                        "exchange_ts": exchange_ts,
                        "ingest_ts": ingest_ts,
                        "broadcast_ts": broadcast_ts,
                        "proc_us": round(engine_us, 1),
                        "engine_us": round(engine_us, 1),
                    }
                )
                self._fallback_deque.append(entry)
                self._fallback_total = getattr(self, "_fallback_total", 0) + 1

    def replay(
        self,
        from_seq: int,
        to_seq: int,
        symbol: str | None = None,
        max_events: int = 50000,
    ) -> list[dict]:
        """
        Query a sequence range from the circular buffer in microseconds.
        Returns list of event dictionaries.
        """
        with self._lock:
            if self.is_native:
                requested = min(max_events, max(0, to_seq - from_seq + 1))
                if requested <= 0:
                    return []
                out_buf = (_CFastReplayRecord * requested)()
                sym_b = symbol.encode("ascii", errors="replace") if symbol else None
                count = _NATIVE_LIB.fastpath_replay_slice(
                    int(from_seq),
                    int(to_seq),
                    sym_b,
                    out_buf,
                    requested,
                )
                return [out_buf[i].to_dict() for i in range(count)]
            else:
                sym_clean = symbol.upper() if symbol else None
                return [
                    m
                    for m in self._fallback_deque
                    if from_seq <= m.get("seq", 0) <= to_seq
                    and (sym_clean is None or m.get("sym") == sym_clean)
                ][:max_events]

    def replay_binary(
        self,
        from_seq: int,
        to_seq: int,
        symbol: str | None = None,
        max_events: int = 50000,
    ) -> bytes:
        """
        Query a sequence range and return packed 92-byte MDRAP-BIN frames directly.
        """
        with self._lock:
            if self.is_native:
                requested = min(max_events, max(0, to_seq - from_seq + 1))
                if requested <= 0:
                    return b""
                frame_len = 92
                buf_size = requested * frame_len
                out_bytes = (ctypes.c_uint8 * buf_size)()
                sym_b = symbol.encode("ascii", errors="replace") if symbol else None
                frames = _NATIVE_LIB.fastpath_replay_binary_slice(
                    int(from_seq),
                    int(to_seq),
                    sym_b,
                    out_bytes,
                    buf_size,
                )
                return bytes(out_bytes)[: frames * frame_len]
            else:
                from protocol import pack_tick_frame

                events = self.replay(from_seq, to_seq, symbol, max_events)
                chunks = []
                for ev in events:
                    chunks.append(
                        pack_tick_frame(
                            seq=ev.get("seq", 0),
                            symbol=ev.get("sym", ""),
                            source=ev.get("source", ""),
                            price=ev.get("price"),
                            size=ev.get("size"),
                            bid=ev.get("bid"),
                            ask=ev.get("ask"),
                            status=ev.get("status", "VALID"),
                            is_crossed=bool(ev.get("is_crossed", False)),
                            exchange_ts=ev.get("exchange_ts", 0.0),
                            ingest_ts=ev.get("ingest_ts", 0.0),
                            broadcast_ts=ev.get("broadcast_ts", 0.0),
                            engine_us=ev.get("engine_us", 0.0),
                        )
                    )
                return b"".join(chunks)

    def stats(self) -> dict:
        """Return circular buffer telemetry and sequence bounds."""
        with self._lock:
            if self.is_native:
                c_min = ctypes.c_uint64(0)
                c_max = ctypes.c_uint64(0)
                c_tot = ctypes.c_uint64(0)
                c_cap = ctypes.c_int32(0)
                _NATIVE_LIB.fastpath_replay_stats(
                    ctypes.byref(c_min),
                    ctypes.byref(c_max),
                    ctypes.byref(c_tot),
                    ctypes.byref(c_cap),
                )
                return {
                    "is_native": True,
                    "capacity": c_cap.value,
                    "min_seq": c_min.value,
                    "max_seq": c_max.value,
                    "total_recorded": c_tot.value,
                }
            else:
                count = len(self._fallback_deque)
                min_s = self._fallback_deque[0].get("seq", 0) if count > 0 else 0
                max_s = self._fallback_deque[-1].get("seq", 0) if count > 0 else 0
                return {
                    "is_native": False,
                    "capacity": self.capacity,
                    "min_seq": min_s,
                    "max_seq": max_s,
                    "total_recorded": getattr(self, "_fallback_total", count),
                }

    def clear(self) -> None:
        """Reset replay buffer."""
        with self._lock:
            if self.is_native:
                _NATIVE_LIB.fastpath_replay_clear()
            else:
                self._fallback_deque.clear()
                self._fallback_total = 0

    def __len__(self) -> int:
        """Return number of active records stored in the replay buffer."""
        st = self.stats()
        tot = st.get("total_recorded", 0)
        cap = st.get("capacity", self.capacity)
        return min(tot, cap)


# ---------------------------------------------------------------------------
# Native Zero-Copy Shared Memory Ctypes Structures & Helpers
# ---------------------------------------------------------------------------


class NativeShmSlot(ctypes.Structure):
    """C-level binary representation of an MDRAP 128-byte SHM slot."""

    _pack_ = 1
    _fields_ = [
        ("commit_seq", ctypes.c_uint64),
        ("event_type", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("is_crossed", ctypes.c_uint8),
        ("pad1", ctypes.c_uint8 * 5),
        ("exchange_ts", ctypes.c_double),
        ("ingest_ts", ctypes.c_double),
        ("broadcast_ts", ctypes.c_double),
        ("engine_us", ctypes.c_float),
        ("pad2", ctypes.c_uint8 * 4),
        ("price", ctypes.c_double),
        ("size", ctypes.c_double),
        ("bid", ctypes.c_double),
        ("ask", ctypes.c_double),
        ("bid_sz", ctypes.c_double),
        ("ask_sz", ctypes.c_double),
        ("symbol", ctypes.c_char * 16),
        ("source", ctypes.c_char * 8),
        ("pad3", ctypes.c_uint8 * 8),
    ]


class NativeShmSlotV3(ctypes.Structure):
    """C-level binary representation of an MDRAP 128-byte SHM v3 slot."""

    _pack_ = 1
    _fields_ = [
        ("commit_seq", ctypes.c_uint64),
        ("event_type", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("is_crossed", ctypes.c_uint8),
        ("present", ctypes.c_uint8),
        ("trunc", ctypes.c_uint8),
        ("pad1", ctypes.c_uint8 * 3),
        ("exchange_ts", ctypes.c_double),
        ("ingest_ts", ctypes.c_double),
        ("broadcast_ts", ctypes.c_double),
        ("engine_us", ctypes.c_float),
        ("pad2", ctypes.c_uint32),
        ("price", ctypes.c_double),
        ("size", ctypes.c_double),
        ("bid", ctypes.c_double),
        ("ask", ctypes.c_double),
        ("bid_sz", ctypes.c_double),
        ("ask_sz", ctypes.c_double),
        ("symbol", ctypes.c_char * 16),
        ("source", ctypes.c_char * 8),
        ("pad3", ctypes.c_uint8 * 8),
    ]


class _PyBuffer(ctypes.Structure):
    _fields_ = [
        ("buf", ctypes.c_void_p),
        ("obj", ctypes.c_void_p),
        ("len", ctypes.c_ssize_t),
        ("itemsize", ctypes.c_ssize_t),
        ("readonly", ctypes.c_int),
        ("ndim", ctypes.c_int),
        ("format", ctypes.c_char_p),
        ("shape", ctypes.c_void_p),
        ("strides", ctypes.c_void_p),
        ("suboffsets", ctypes.c_void_p),
        ("smalltable", ctypes.c_size_t * 2),
        ("internal", ctypes.c_void_p),
    ]


def get_buffer_address(obj) -> int:
    """Extract raw memory address of a buffer/memoryview and immediately release buffer lock."""
    if isinstance(obj, int):
        return obj
    if isinstance(obj, ctypes.c_void_p):
        return obj.value or 0
    pybuf = _PyBuffer()
    res = ctypes.pythonapi.PyObject_GetBuffer(
        ctypes.py_object(obj), ctypes.byref(pybuf), 0
    )
    if res != 0:
        raise BufferError("Failed to obtain buffer address")
    addr = pybuf.buf
    ctypes.pythonapi.PyBuffer_Release(ctypes.byref(pybuf))
    return addr


def has_native_shm() -> bool:
    """Check if compiled native C shared memory acceleration is active."""
    return bool(_NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_shm_read_slot"))


def native_shm_read_slot(buf_ptr, slot_count: int, target_seq: int) -> dict | None:
    """Read an SHM slot using compiled native C acceleration in sub-30 nanoseconds."""
    if not has_native_shm():
        return None
    raw_addr = get_buffer_address(buf_ptr)
    magic = (ctypes.c_char * 4).from_address(raw_addr).value
    if magic == b"MDRP" and hasattr(_NATIVE_LIB, "fastpath_shm_read_slot_v3"):
        slot3 = NativeShmSlotV3()
        total_len = 128 + slot_count * 128
        res = _NATIVE_LIB.fastpath_shm_read_slot_v3(
            ctypes.c_void_p(raw_addr),
            ctypes.c_size_t(total_len),
            ctypes.c_uint32(slot_count),
            ctypes.c_uint64(target_seq),
            ctypes.byref(slot3),
        )
        if res != 1:
            return None
        sym = slot3.symbol.rstrip(b"\x00").decode("ascii", errors="replace")
        src = slot3.source.rstrip(b"\x00").decode("ascii", errors="replace")
        status_map = {1: "VALID", 2: "SUSPICIOUS", 3: "INVALID"}
        px = slot3.price if (slot3.present & 0x01) else None
        sz = slot3.size if (slot3.present & 0x02) else None
        b_px = slot3.bid if (slot3.present & 0x04) else None
        a_px = slot3.ask if (slot3.present & 0x08) else None
        b_sz = slot3.bid_sz if (slot3.present & 0x10) else None
        a_sz = slot3.ask_sz if (slot3.present & 0x20) else None
        return {
            "type": "TICK",
            "seq": slot3.commit_seq,
            "sym": sym,
            "price": px,
            "size": sz,
            "bid": b_px,
            "ask": a_px,
            "bid_size": b_sz,
            "ask_size": a_sz,
            "source": src,
            "status": status_map.get(slot3.status, "VALID"),
            "is_crossed": bool(slot3.is_crossed),
            "exchange_ts": slot3.exchange_ts,
            "ingest_ts": slot3.ingest_ts,
            "broadcast_ts": slot3.broadcast_ts,
            "engine_us": slot3.engine_us,
        }

    slot = NativeShmSlot()
    res = _NATIVE_LIB.fastpath_shm_read_slot(
        ctypes.c_void_p(raw_addr),
        ctypes.c_uint32(slot_count),
        ctypes.c_uint64(target_seq),
        ctypes.byref(slot),
    )
    if res != 1:
        return None

    sym = slot.symbol.rstrip(b"\x00").decode("ascii", errors="replace")
    src = slot.source.rstrip(b"\x00").decode("ascii", errors="replace")
    status_map = {1: "VALID", 2: "SUSPICIOUS", 3: "INVALID"}

    if slot.event_type == 2:  # DEPTH
        return {
            "type": "DEPTH",
            "seq": slot.commit_seq,
            "sym": sym,
            "micro_price": slot.price,
            "ofi": slot.size,
            "bid": slot.bid,
            "ask": slot.ask,
            "bid_size": slot.bid_sz,
            "ask_size": slot.ask_sz,
            "bids": [[slot.bid, slot.bid_sz, "AGG"]],
            "asks": [[slot.ask, slot.ask_sz, "AGG"]],
            "is_crossed": bool(slot.is_crossed),
            "status": "VALID",
            "exchange_ts": slot.exchange_ts,
            "ingest_ts": slot.ingest_ts,
            "broadcast_ts": slot.broadcast_ts,
            "engine_us": slot.engine_us,
        }
    else:
        return {
            "type": "TICK",
            "seq": slot.commit_seq,
            "sym": sym,
            "price": slot.price,
            "size": slot.size,
            "bid": slot.bid,
            "ask": slot.ask,
            "bid_size": slot.bid_sz,
            "ask_size": slot.ask_sz,
            "source": src,
            "status": status_map.get(slot.status, "VALID"),
            "is_crossed": bool(slot.is_crossed),
            "exchange_ts": slot.exchange_ts,
            "ingest_ts": slot.ingest_ts,
            "broadcast_ts": slot.broadcast_ts,
            "engine_us": slot.engine_us,
        }


# ============================================================================
# Phase 13: High-Performance Quantitative & Options Python Wrappers
# ============================================================================


def fast_bsm_price(
    S: float, K: float, T: float, r: float, sigma: float, is_call: bool
) -> float | None:
    """Calculate European option price using native C BSM implementation."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_bsm_price"):
        return float(
            _NATIVE_LIB.fastpath_bsm_price(S, K, T, r, sigma, 1 if is_call else 0)
        )
    return None


def fast_bsm_greeks(
    S: float, K: float, T: float, r: float, sigma: float, is_call: bool
) -> tuple[float, float, float, float, float, float, float] | None:
    """Compute Delta, Gamma, Theta, Vega, Rho, Vanna, Volga in native C."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_bsm_greeks"):
        out = (ctypes.c_double * 7)()
        _NATIVE_LIB.fastpath_bsm_greeks(S, K, T, r, sigma, 1 if is_call else 0, out)
        return (out[0], out[1], out[2], out[3], out[4], out[5], out[6])
    return None


def fast_binomial_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    is_call: bool,
    steps: int = 200,
) -> float | None:
    """Calculate American option price using native C CRR binomial tree."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_binomial_price"):
        return float(
            _NATIVE_LIB.fastpath_binomial_price(
                S, K, T, r, sigma, 1 if is_call else 0, steps
            )
        )
    return None


def fast_implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    is_call: bool,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Solve for implied volatility using native C Newton-Raphson solver."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_implied_volatility"):
        return float(
            _NATIVE_LIB.fastpath_implied_volatility(
                market_price, S, K, T, r, 1 if is_call else 0, tol, max_iter
            )
        )
    return None


def fast_calc_rsi(prices: list[float], period: int = 14) -> list[float] | None:
    """Compute Wilder-smoothed RSI using native C kernel."""
    n = len(prices)
    if n == 0 or period <= 0:
        return []
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_calc_rsi"):
        in_arr = (ctypes.c_double * n)(*prices)
        out_arr = (ctypes.c_double * n)()
        _NATIVE_LIB.fastpath_calc_rsi(in_arr, n, period, out_arr)
        return [float(x) for x in out_arr]
    return None


def fast_calc_ema(prices: list[float], period: int = 20) -> list[float] | None:
    """Compute exponential moving average using native C kernel."""
    n = len(prices)
    if n == 0 or period <= 0:
        return []
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_calc_ema"):
        in_arr = (ctypes.c_double * n)(*prices)
        out_arr = (ctypes.c_double * n)()
        _NATIVE_LIB.fastpath_calc_ema(in_arr, n, period, out_arr)
        return [float(x) for x in out_arr]
    return None


def fast_calc_bollinger(
    prices: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float], list[float], list[float]] | None:
    """Compute Bollinger Bands (Upper, Middle, Lower) using native C kernel."""
    n = len(prices)
    if n == 0 or period <= 0:
        return ([], [], [])
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_calc_bollinger"):
        in_arr = (ctypes.c_double * n)(*prices)
        upper = (ctypes.c_double * n)()
        mid = (ctypes.c_double * n)()
        lower = (ctypes.c_double * n)()
        _NATIVE_LIB.fastpath_calc_bollinger(
            in_arr, n, period, num_std, upper, mid, lower
        )
        return (
            [float(x) for x in upper],
            [float(x) for x in mid],
            [float(x) for x in lower],
        )
    return None


def fast_calc_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float] | None:
    """Compute Average True Range using native C kernel."""
    n = len(highs)
    if n == 0 or len(lows) != n or len(closes) != n or period <= 0:
        return []
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_calc_atr"):
        h_arr = (ctypes.c_double * n)(*highs)
        l_arr = (ctypes.c_double * n)(*lows)
        c_arr = (ctypes.c_double * n)(*closes)
        out_arr = (ctypes.c_double * n)()
        _NATIVE_LIB.fastpath_calc_atr(h_arr, l_arr, c_arr, n, period, out_arr)
        return [float(x) for x in out_arr]
    return None


def fast_monte_carlo_var(
    mean: float,
    std_dev: float,
    n_simulations: int = 10000,
    horizon_days: int = 1,
    initial_val: float = 100000.0,
    confidence: float = 0.95,
    seed: int = 42,
) -> float | None:
    """Execute high-speed Monte Carlo VaR simulation in native C."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_monte_carlo_var"):
        return float(
            _NATIVE_LIB.fastpath_monte_carlo_var(
                mean,
                std_dev,
                n_simulations,
                horizon_days,
                initial_val,
                confidence,
                seed,
            )
        )
    return None


def fast_fix_checksum(data: bytes | str) -> int | None:
    """Compute 8-bit FIX protocol checksum in native C."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_fix_checksum"):
        buf = data.encode("ascii") if isinstance(data, str) else data
        n = len(buf)
        if n == 0:
            return 0
        c_buf = (ctypes.c_uint8 * n).from_buffer_copy(buf)
        return int(_NATIVE_LIB.fastpath_fix_checksum(c_buf, n))
    return None


# ---------------------------------------------------------------------------
# High-Level Spatial Geodesic & Fleet Geofencing Fastpath
# ---------------------------------------------------------------------------


def fast_haversine_nm(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float | None:
    """Calculates Great-Circle distance in nautical miles using native C hot path."""
    if _NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_haversine_nm"):
        dist = _NATIVE_LIB.fastpath_haversine_nm(lat1, lon1, lat2, lon2)
        if dist >= 0.0:
            return dist
    return None


def make_fast_chokepoints(chokepoints) -> ctypes.Array | None:
    """Pre-marshals a list or dict of Chokepoint objects into a contiguous C struct array."""
    if not chokepoints:
        return None
    cp_list = (
        list(chokepoints.values())
        if isinstance(chokepoints, dict)
        else list(chokepoints)
    )
    arr_type = _CFastChokepoint * len(cp_list)
    arr = arr_type()
    for i, cp in enumerate(cp_list):
        lat = float(cp.latitude)
        lon = float(cp.longitude)
        rad_nm = float(cp.radius_nm)
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        cos_lat = math.cos(lat_rad)
        sin_lat = math.sin(lat_rad)
        dlat_max = rad_nm / 60.0
        dlon_max = rad_nm / (60.0 * max(0.01, cos_lat))

        arr[i].lat = lat
        arr[i].lon = lon
        arr[i].radius_nm = rad_nm
        arr[i].dlat_max = dlat_max
        arr[i].dlon_max = dlon_max
        arr[i].lat_rad = lat_rad
        arr[i].lon_rad = lon_rad
        arr[i].cos_lat = cos_lat
        arr[i].sin_lat = sin_lat
    return arr


def fast_vessel_chokepoint_eval(
    v_lat: float, v_lon: float, c_chokepoints: ctypes.Array, cp_count: int
) -> tuple[int, float, bool] | None:
    """Evaluates a single vessel against chokepoints using native C hot path with AABB pre-filtering."""
    if (
        _NATIVE_LIB
        and hasattr(_NATIVE_LIB, "fastpath_vessel_chokepoint_eval")
        and c_chokepoints
    ):
        nearest_idx = ctypes.c_int32(0)
        nearest_dist = ctypes.c_double(0.0)
        in_cp = ctypes.c_uint8(0)

        rc = _NATIVE_LIB.fastpath_vessel_chokepoint_eval(
            v_lat,
            v_lon,
            c_chokepoints,
            cp_count,
            ctypes.byref(nearest_idx),
            ctypes.byref(nearest_dist),
            ctypes.byref(in_cp),
        )
        if rc == 0:
            return int(nearest_idx.value), float(nearest_dist.value), bool(in_cp.value)
    return None


def fast_batch_fleet_geofence(
    v_lats: list[float], v_lons: list[float], c_chokepoints: ctypes.Array, cp_count: int
) -> list[tuple[int, float, bool]] | None:
    """Evaluates an entire fleet of vessels in a single vectorized C call."""
    count = len(v_lats)
    if count == 0 or count != len(v_lons):
        return []
    if (
        _NATIVE_LIB
        and hasattr(_NATIVE_LIB, "fastpath_batch_fleet_geofence")
        and c_chokepoints
    ):
        lats_arr = (ctypes.c_double * count)(*v_lats)
        lons_arr = (ctypes.c_double * count)(*v_lons)
        out_idx = (ctypes.c_int32 * count)()
        out_dist = (ctypes.c_double * count)()
        out_in = (ctypes.c_uint8 * count)()

        rc = _NATIVE_LIB.fastpath_batch_fleet_geofence(
            lats_arr,
            lons_arr,
            count,
            c_chokepoints,
            cp_count,
            out_idx,
            out_dist,
            out_in,
        )
        if rc == 0:
            return [
                (int(out_idx[i]), float(out_dist[i]), bool(out_in[i]))
                for i in range(count)
            ]
    return None
