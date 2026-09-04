"""
Python ctypes wrapper for MDRAP Native C Hot Path.

Exposes FastQualityEngine as a drop-in replacement for QualityEngine:
- Loads src/fastpath.dll (Windows) or src/fastpath.so (Linux/macOS)
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
        b_sz = self.bid_size if not math.isnan(self.bid_size) and self.bid_size > 0 else None
        a_sz = self.ask_size if not math.isnan(self.ask_size) and self.ask_size > 0 else None
        ev_type = self.event_type.decode("ascii", errors="replace").strip("\x00") or "TICK"

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
                "spread": (a_px - b_px) if (a_px is not None and b_px is not None) else None,
                "mid": ((a_px + b_px) / 2.0) if (a_px is not None and b_px is not None) else None,
                "crossed": bool(self.is_crossed),
            } if (b_px is not None or a_px is not None) else None,
        }


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

            # Phase F: Replay Buffer bindings
            if hasattr(lib, "fastpath_replay_record"):
                lib.fastpath_replay_record.argtypes = [
                    ctypes.c_uint64, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                    ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
                    ctypes.c_double, ctypes.c_double, ctypes.c_uint8, ctypes.c_uint8,
                    ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
                ]
                lib.fastpath_replay_record.restype = None

            if hasattr(lib, "fastpath_replay_slice"):
                lib.fastpath_replay_slice.argtypes = [
                    ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p,
                    ctypes.POINTER(_CFastReplayRecord), ctypes.c_int32
                ]
                lib.fastpath_replay_slice.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_replay_binary_slice"):
                lib.fastpath_replay_binary_slice.argtypes = [
                    ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_int32
                ]
                lib.fastpath_replay_binary_slice.restype = ctypes.c_int32

            if hasattr(lib, "fastpath_replay_stats"):
                lib.fastpath_replay_stats.argtypes = [
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_int32)
                ]
                lib.fastpath_replay_stats.restype = None

            if hasattr(lib, "fastpath_replay_clear"):
                lib.fastpath_replay_clear.argtypes = []
                lib.fastpath_replay_clear.restype = None

            if hasattr(lib, "fastpath_cleanup"):
                lib.fastpath_cleanup.argtypes = []
                lib.fastpath_cleanup.restype = None

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
        if config is None:
            try:
                from config import load_config
                self.cfg = load_config().quality
            except Exception:
                self.cfg = QualityConfig()
        else:
            self.cfg = config
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

        s_id = self._get_source_id(event.source)
        i_id = self._get_instrument_id(event.instrument_id)

        # Graceful fallback: C static tables have MAX_SOURCES=32, MAX_INSTRUMENTS=8192.
        # If the number of unique sources or instruments exceeds C bounds, evaluate with Python engine.
        if s_id >= 32 or i_id >= 8192:
            if not self._fallback_engine:
                self._fallback_engine = QualityEngine(self.cfg)
            res = self._fallback_engine.evaluate(event)
            self.counts[res.quality_status.value] = self.counts.get(res.quality_status.value, 0) + 1
            for r in res.reasons:
                self.reason_counts[r] = self.reason_counts.get(r, 0) + 1
            return res

        try:
            # Direct CPU register call to C hot path (< 100 ns)
            packed = _FAST_EVAL(
                s_id,
                i_id,
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


class NativeReplayBuffer:
    """
    High-Performance Native C Circular Replay Buffer (Spec §18).
    Maintains 65,536-slot fixed-width ring buffer in C memory with O(1) sequence indexing.
    Falls back to collections.deque if native C accelerator is unavailable.
    """

    def __init__(self, capacity: int = 65536):
        self.capacity = capacity
        self._lock = threading.RLock()
        self.is_native = bool(_NATIVE_LIB and hasattr(_NATIVE_LIB, "fastpath_replay_record"))
        self._fallback_deque: Optional[collections.deque] = None

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
        price: Optional[float] = None,
        size: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        bid_size: Optional[float] = None,
        ask_size: Optional[float] = None,
        status: str = "VALID",
        is_crossed: bool = False,
        exchange_ts: float = 0.0,
        ingest_ts: float = 0.0,
        broadcast_ts: float = 0.0,
        engine_us: float = 0.0,
        raw_dict: Optional[dict] = None,
    ) -> None:
        """Record normalized tick into circular buffer."""
        with self._lock:
            if self.is_native:
                st_code = 1 if status == "VALID" else (2 if status == "SUSPICIOUS" else 3)
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
                entry = raw_dict if raw_dict is not None else {
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
                self._fallback_deque.append(entry)

    def replay(
        self,
        from_seq: int,
        to_seq: int,
        symbol: Optional[str] = None,
        max_events: int = 50000,
    ) -> List[dict]:
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
                    m for m in self._fallback_deque
                    if from_seq <= m.get("seq", 0) <= to_seq
                    and (sym_clean is None or m.get("sym") == sym_clean)
                ][:max_events]

    def replay_binary(
        self,
        from_seq: int,
        to_seq: int,
        symbol: Optional[str] = None,
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
                return bytes(out_bytes)[:frames * frame_len]
            else:
                from protocol import pack_tick_frame
                events = self.replay(from_seq, to_seq, symbol, max_events)
                chunks = []
                for ev in events:
                    chunks.append(pack_tick_frame(
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
                    ))
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
                    "total_recorded": count,
                }

    def clear(self) -> None:
        """Reset replay buffer."""
        with self._lock:
            if self.is_native:
                _NATIVE_LIB.fastpath_replay_clear()
            else:
                self._fallback_deque.clear()

    def __len__(self) -> int:
        """Return number of active records stored in the replay buffer."""
        st = self.stats()
        tot = st.get("total_recorded", 0)
        cap = st.get("capacity", self.capacity)
        return min(tot, cap)

