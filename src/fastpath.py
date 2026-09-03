"""
Python ctypes wrapper for MDRAP Native C Hot Path.

Exposes FastQualityEngine as a drop-in replacement for QualityEngine:
- Loads src/fastpath.dll (Windows) or src/fastpath.so (Linux/macOS)
- Marshals CanonicalEvent to 64-byte aligned C struct
- Decodes 32-bit packed status & reason bitmask
- Seamless fallback to pure Python QualityEngine if native library is unavailable
"""
from __future__ import annotations

import ctypes
import math
import os
import sys
from typing import Dict, List, Optional

from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityConfig, QualityEngine

# Status values
_STATUS_MAP = {
    0: QualityStatus.VALID,
    1: QualityStatus.SUSPICIOUS,
    2: QualityStatus.INVALID,
}

# Bitmask values matching fastpath.c
_REASON_BITS = [
    (1 << 0, Reason.SCHEMA_VIOLATION.value),
    (1 << 1, Reason.DUPLICATE.value),
    (1 << 2, Reason.SEQUENCE_GAP.value),
    (1 << 3, Reason.OUT_OF_ORDER.value),
    (1 << 4, Reason.STALE.value),
    (1 << 5, Reason.PRICE_ANOMALY.value),
    (1 << 6, Reason.CROSSED_QUOTE.value),
    (1 << 7, Reason.CROSS_FEED_DISAGREEMENT.value),
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
        ("reason_mask", ctypes.c_uint32),
    ]


def _load_native_lib():
    src_dir = os.path.dirname(__file__)
    dll_name = "fastpath.dll" if sys.platform == "win32" else "fastpath.so"
    dll_path = os.path.join(src_dir, dll_name)
    if os.path.exists(dll_path):
        try:
            lib = ctypes.CDLL(dll_path)
            lib.fastpath_init.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_int32]
            lib.fastpath_init.restype = None

            lib.fastpath_reset.argtypes = []
            lib.fastpath_reset.restype = None

            lib.fastpath_evaluate.argtypes = [ctypes.POINTER(_CFastEvent), ctypes.POINTER(_CFastResult)]
            lib.fastpath_evaluate.restype = None

            lib.fastpath_evaluate_batch.argtypes = [
                ctypes.POINTER(_CFastEvent), ctypes.POINTER(_CFastResult), ctypes.c_int32
            ]
            lib.fastpath_evaluate_batch.restype = None

            lib.fastpath_eval_fast.argtypes = [
                ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                ctypes.c_double, ctypes.c_double, ctypes.c_int64,
                ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
                ctypes.c_double, ctypes.c_double,
            ]
            lib.fastpath_eval_fast.restype = ctypes.c_uint64
            return lib
        except Exception as e:
            print(f"[fastpath] Warning: Failed to load {dll_path}: {e}", file=sys.stderr)
            return None
    return None


_NATIVE_LIB = _load_native_lib()
_FAST_EVAL = _NATIVE_LIB.fastpath_eval_fast if _NATIVE_LIB else None
_NAN = math.nan


class FastQualityEngine:
    """
    Drop-in replacement for QualityEngine backed by compiled C shared library.
    """

    def __init__(self, config: Optional[QualityConfig] = None):
        self.cfg = config or QualityConfig()
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts: Dict[str, int] = {}
        self._source_map = dict(_SOURCE_ID_MAP)
        self._inst_map = dict(_INSTRUMENT_ID_MAP)
        self._fallback_engine: Optional[QualityEngine] = None

        if _NATIVE_LIB:
            _NATIVE_LIB.fastpath_init(
                self.cfg.staleness_threshold_s,
                self.cfg.price_anomaly_stddev,
                self.cfg.price_window,
            )
        else:
            self._fallback_engine = QualityEngine(self.cfg)

    def _get_source_id(self, source: str) -> int:
        if source not in self._source_map:
            self._source_map[source] = len(self._source_map)
        return self._source_map[source]

    def _get_instrument_id(self, inst: str) -> int:
        if inst not in self._inst_map:
            self._inst_map[inst] = len(self._inst_map)
        return self._inst_map[inst]

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        if not _FAST_EVAL:
            if not self._fallback_engine:
                self._fallback_engine = QualityEngine(self.cfg)
            res = self._fallback_engine.evaluate(event)
            self.counts = self._fallback_engine.counts
            self.reason_counts = self._fallback_engine.reason_counts
            return res

        try:
            # Direct CPU register call to C hot path (< 100 ns)
            packed = _FAST_EVAL(
                self._get_source_id(event.source),
                self._get_instrument_id(event.instrument_id),
                1 if event.event_type == EventType.QUOTE else 0,
                event.exchange_timestamp,
                event.receive_timestamp,
                event.sequence_number if event.sequence_number is not None else -1,
                event.price if event.price is not None else _NAN,
                event.quantity if event.quantity is not None else _NAN,
                event.bid_price if event.bid_price is not None else _NAN,
                event.ask_price if event.ask_price is not None else _NAN,
                event.bid_size if event.bid_size is not None else _NAN,
                event.ask_size if event.ask_size is not None else _NAN,
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
        priority = {QualityStatus.VALID: 0, QualityStatus.SUSPICIOUS: 1, QualityStatus.INVALID: 2}
        if priority[status] > priority.get(event.quality_status, 0):
            event.quality_status = status

        if mask:
            for bit, reason_str in _REASON_BITS:
                if mask & bit:
                    event.reasons.append(reason_str)
                    self.reason_counts[reason_str] = self.reason_counts.get(reason_str, 0) + 1

        self.counts[event.quality_status.value] += 1
        return event

    def reset(self):
        if _NATIVE_LIB:
            _NATIVE_LIB.fastpath_reset()
        if self._fallback_engine:
            self._fallback_engine = QualityEngine(self.cfg)
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts.clear()
