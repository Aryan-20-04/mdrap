"""
MDRAP Decoupled Zero-Copy Shared Memory (SHM) Ring Buffer Engine v3 (Spec §18).

Provides sub-microsecond IPC market data delivery for co-located
institutional trading algorithms using memory-mapped ring buffers.

Key Architectural Guarantees:
1. Lock-Free Single-Producer Multi-Consumer (SPMC) design.
2. Two-Phase Commit Protocol: payload written before atomic sequence commit.
3. Cache-Line Alignment (64-byte padded writer & heartbeat lines) to eliminate false sharing.
4. Epoch Generation Tracking: automatic detection of publisher restarts.
5. Overrun & Lap Detection: slow readers safely skip forward with telemetry.
6. Decoupled Fault Isolation: reader crashes cannot block or poison the writer.
7. Presence Bitmask: None values preserved without inventing 0.0 prices/sizes.
"""

from __future__ import annotations

from collections.abc import Generator
from enum import Enum
import hashlib
import logging
import os
import platform
import re
import secrets
import struct
import sys
import time
from dataclasses import dataclass

logger = logging.getLogger("mdrap.shm")


def normalize_shm_name(name: str) -> str:
    """
    Normalize shared memory name to ensure cross-platform POSIX compliance (macOS Darwin limit: 31 chars).
    Produces identical normalized names for writers and readers deriving from the same input.
    Avoids silent truncation and preserves collision resistance via SHA-256 digest hashing when length exceeds limit.
    """
    if not name:
        return "mdrap_feed"
    clean = name.strip()
    # Strip any leading slashes for normalization
    bare = clean.lstrip("/")
    # If bare name is already valid POSIX length (<= 30 chars) and contains only safe chars
    if len(bare) <= 30 and re.match(r"^[A-Za-z0-9_.-]+$", bare):
        return bare

    # For long or special-character names, compute stable digest
    # Prefix (up to 12 chars sanitized) + '_' + 16-char hex digest = max 29 chars
    prefix_raw = re.sub(r"[^A-Za-z0-9_]", "", bare)[:12].rstrip("_")
    if not prefix_raw:
        prefix_raw = "shm"
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
    return f"{prefix_raw}_{digest}"


class ReaderState(str, Enum):
    """Lifecycle state machine for SHMReader."""

    DETACHED = "DETACHED"
    ATTACHED = "ATTACHED"
    INVALIDATED = "INVALIDATED"
    REATTACHING = "REATTACHING"


def _safe_unregister(name: str) -> None:
    """Safely unregister shared memory name from multiprocessing resource_tracker."""
    try:
        from multiprocessing import resource_tracker

        target = "/" + name.lstrip("/")
        try:
            resource_tracker.unregister(target, "shared_memory")
        except Exception:
            pass
    except Exception:
        pass


try:
    from multiprocessing.shared_memory import SharedMemory

    HAS_SHM = True
except ImportError:
    HAS_SHM = False

try:
    from . import fastpath

    HAS_FASTPATH = getattr(fastpath, "HAS_FASTPATH", False)
except (ImportError, AttributeError):
    fastpath = None
    HAS_FASTPATH = False

__stability__ = "stable"


def _check_arm_shm_safety() -> None:
    """Fail closed on weak-memory architectures (ARM64) if native C atomics are unavailable."""
    arch = platform.machine().lower()
    if arch in ("aarch64", "arm64", "armv8l", "armv8"):
        allow_unsafe = os.environ.get("MDRAP_UNSAFE_SHM", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        if not allow_unsafe:
            try:
                from .fastpath import HAS_FASTPATH

                if not HAS_FASTPATH:
                    raise RuntimeError(
                        f"Refusing to run lock-free Shared Memory on ARM64 ({arch}) without native C atomics. "
                        "Weak memory ordering can cause silent data corruption or torn reads. "
                        "Compile the native C fastpath extension or set MDRAP_UNSAFE_SHM=1 to override."
                    )
            except (ImportError, AttributeError):
                raise RuntimeError(
                    f"Refusing to run lock-free Shared Memory on ARM64 ({arch}) without native C atomics. "
                    "Weak memory ordering can cause silent data corruption or torn reads. "
                    "Compile the native C fastpath extension or set MDRAP_UNSAFE_SHM=1 to override."
                )


# ---------------------------------------------------------------------------
# Memory Layout & Cache-Line Alignment Constants (SHM v3)
# ---------------------------------------------------------------------------
MAGIC = b"MDRP"
VERSION = 3
DEFAULT_SLOT_COUNT = 16384  # Must be power of 2 for fast bitwise masking
SLOT_SIZE = 128  # Cache-line aligned (2 x 64 bytes)
HEADER_SIZE = 128  # Cache-line aligned (2 x 64 bytes)
TOTAL_SHM_SIZE = HEADER_SIZE + (DEFAULT_SLOT_COUNT * SLOT_SIZE)
UNCOMMITTED = 0xFFFFFFFFFFFFFFFF

# Presence Bitmask for Optional Numeric Fields
SHM3_PRESENT_PRICE = 0x01
SHM3_PRESENT_SIZE = 0x02
SHM3_PRESENT_BID = 0x04
SHM3_PRESENT_ASK = 0x08
SHM3_PRESENT_BSZ = 0x10
SHM3_PRESENT_ASZ = 0x20

# Cache Line 1 (64 bytes) - Writer Hot Line (v3 aligned layout):
# magic(4s), version(H=2), slot_size(H=2), slot_count(I=4), reserved(I=4),
# epoch_id(Q=8) @ 16, head_seq(Q=8) @ 24, pad32(32s)
# 4 + 2 + 2 + 4 + 4 + 8 + 8 + 32 = 64 bytes
HEADER_LINE1_STRUCT = struct.Struct("<4sHHIIQQ32s")

# Cache Line 2 (64 bytes) - Heartbeat & Diagnostics Line:
# heartbeat_ts(d=8) @ 64, dropped_ticks(Q=8) @ 72, flags(I=4) @ 80, pad44(44s) @ 84
# 8 + 8 + 4 + 44 = 64 bytes
HEADER_LINE2_STRUCT = struct.Struct("<dQI44s")
FLAGS_OFFSET = 80
SHM_FLAG_WATERMARK_WARNING = 0x01
DEFAULT_SHM_WATERMARK_PCT = float(os.environ.get("MDRAP_SHM_WATERMARK_PCT", "0.80"))

# Slot (128 bytes, 2 cache lines):
# commit_seq(Q=8)
# event_type(B=1), status(B=1), is_crossed(B=1), present(B=1), trunc(B=1), pad1(3s=3) -> 8B (offset 8..16)
# exchange_ts(d=8), ingest_ts(d=8), broadcast_ts(d=8) -> 24B (offset 16..40)
# engine_us(f=4), pad2(I=4) -> 8B (offset 40..48)
# price(d=8), size(d=8), bid(d=8), ask(d=8), bid_sz(d=8), ask_sz(d=8) -> 48B (offset 48..96)
# symbol(16s=16), source(8s=8), pad3(8s=8) -> 32B (offset 96..128)
SLOT_STRUCT = struct.Struct("<QBBBBB3sdddfIdddddd16s8s8s")
PAYLOAD_STRUCT = struct.Struct("<BBBBB3sdddfIdddddd16s8s8s")

EVENT_TYPE_TICK = 1
EVENT_TYPE_DEPTH = 2

STATUS_MAP_REV = {"UNKNOWN": 0, "VALID": 1, "SUSPICIOUS": 2, "INVALID": 3}
STATUS_MAP_FWD = {0: "UNKNOWN", 1: "VALID", 2: "SUSPICIOUS", 3: "INVALID"}

# ---------------------------------------------------------------------------
# SHM v3 Canonical Source Registry (8-byte wire field preservation)
# ---------------------------------------------------------------------------
# SHM v3 fixed 128-byte slot layout allocates an 8-byte ASCII `source` field.
# Sources <= 8 ASCII bytes (not starting with "@MDRP") are written verbatim.
# Registered >8-byte canonical sources map deterministically to reserved 8-char
# printable ASCII tokens ("@MDRP001", ...), preserving HEADER_SIZE==128,
# SLOT_SIZE==128, VERSION==3, and 100% parity between C fastpath and Python.
SHM_SOURCE_WIRE_LIMIT = 8
SHM_SOURCE_RESERVED_PREFIX = "@MDRP"

CANONICAL_SOURCE_TO_WIRE_V3: dict[str, str] = {
    "DATABENTO": "@MDRP001",
    "COINBASE_PRO": "@MDRP002",
    "NASDAQ_ITCH": "@MDRP003",
    "NASDAQ_ITCH50": "@MDRP004",
    "NYMEX_FUTURES": "@MDRP005",
    "OPRA_CHICAGO": "@MDRP006",
    "FEED_ROTATING": "@MDRP007",
    "PRIMARY_FEED": "@MDRP008",
    "STANDBY_FEED": "@MDRP009",
    "DATABENTO-GLBX": "@MDRP010",
    "EXCHANGE_A": "@MDRP011",
    "EXCHANGE_FEED": "@MDRP012",
    "EXCHANGE_SOAK": "@MDRP013",
    "FEED_ADVERSARIAL": "@MDRP014",
    "FEED_BID_ONLY": "@MDRP015",
    "FEED_BURST": "@MDRP016",
    "FEED_CHAOS": "@MDRP017",
    "FEED_EXCHANGE": "@MDRP018",
    "FEED_HMAC": "@MDRP019",
    "FEED_LEADER": "@MDRP020",
    "FEED_SHARED": "@MDRP021",
    "FEED_TEST": "@MDRP022",
    "AUDIT_FEED": "@MDRP023",
    "BURST_FEED": "@MDRP024",
    "CLEAN_FEED": "@MDRP025",
    "FAULT_FEED": "@MDRP026",
    "LIVE_FEED": "@MDRP027",
    "MALFORMED_FEED": "@MDRP028",
    "METAMORPHIC_FEED": "@MDRP029",
    "SECURE_FEED": "@MDRP030",
    "SLOW_FEED": "@MDRP031",
    "TEST_FEED": "@MDRP032",
    "TEST_EXCHANGE": "@MDRP033",
    "TEST_EXCH": "@MDRP034",
    "TEST_VENUE": "@MDRP035",
    "HOOK_VENUE": "@MDRP036",
    "UNKNOWN_EXCHANGE": "@MDRP037",
    "SIMULATOR": "@MDRP038",
    "REPLAY_SIM": "@MDRP039",
    "LOCAL_DAEMON": "@MDRP040",
    "GATEWAY_RECV": "@MDRP041",
    "POLYGON-AGG": "@MDRP042",
    "POLYGON-FINRA_TRF": "@MDRP043",
    "POLYGON-NASDAQ": "@MDRP044",
    "POLYGON_EQUITIES": "@MDRP045",
    "POLYGON_EXCHANGE_MAP": "@MDRP046",
    "CLOCK_REPRO": "@MDRP047",
    "CRASH_REPRO": "@MDRP048",
    "CRASH_TEST": "@MDRP049",
    "POISON_REPRO": "@MDRP050",
    "NON_DICT_REPRO": "@MDRP051",
    "SHM_FEED_1": "@MDRP052",
    "PUBLISHER_MAP": "@MDRP053",
    "HOST_SYS_CLOCK": "@MDRP054",
    "MDRAP_PROVENANCE_RETRY": "@MDRP055",
    "SECURITY_REJECT": "@MDRP056",
    "ALLOWED_VENUES": "@MDRP057",
    "EQUITIES (SIM)": "@MDRP058",
    "CRYPTO (SIM)": "@MDRP059",
    "DIFF_TEST": "@MDRP060",
    "POST_TEST": "@MDRP061",
}
WIRE_TO_CANONICAL_SOURCE_V3: dict[str, str] = {
    wire: canon for canon, wire in CANONICAL_SOURCE_TO_WIRE_V3.items()
}


def encode_shm_source(source: str | None) -> str:
    """
    Encode a canonical source string into an 8-byte v3 wire token.

    - Rejects literal inputs starting with '@MDRP' to prevent spoofing/collisions.
    - Maps registered >8-byte canonical sources via CANONICAL_SOURCE_TO_WIRE_V3.
    - Passes <=8-byte ASCII sources through unchanged.
    - Raises ValueError for unregistered >8-byte or non-ASCII sources.
    """
    src = "" if source is None else source
    if not isinstance(src, str):
        raise ValueError(f"SHM source must be str, got {type(source).__name__}")
    if src.startswith(SHM_SOURCE_RESERVED_PREFIX):
        raise ValueError(
            f"SHM source {src!r} uses reserved wire prefix {SHM_SOURCE_RESERVED_PREFIX!r}"
        )
    mapped = CANONICAL_SOURCE_TO_WIRE_V3.get(src)
    if mapped is not None:
        return mapped
    try:
        raw = src.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"SHM source must be ASCII, got {src!r}") from exc
    if len(raw) > SHM_SOURCE_WIRE_LIMIT:
        raise ValueError(
            f"Unregistered SHM source {src!r} ({len(raw)} bytes) exceeds "
            f"{SHM_SOURCE_WIRE_LIMIT}-byte v3 wire limit"
        )
    return src


SHM_SYMBOL_WIRE_LIMIT = 16


def encode_shm_symbol(symbol: str | None) -> bytes:
    """
    Encode a symbol string into a fixed <=16-byte UTF-8 v3 wire buffer.

    Never splits a multi-byte UTF-8 code point and rejects symbols whose
    lossless UTF-8 representation exceeds 16 bytes (or contains NUL/surrogates).
    """
    sym = "" if symbol is None else symbol
    if not isinstance(sym, str):
        raise ValueError(f"SHM symbol must be str, got {type(symbol).__name__}")
    if "\x00" in sym:
        raise ValueError("SHM symbol must not contain embedded NUL bytes")
    try:
        raw = sym.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"SHM symbol must be valid UTF-8, got {sym!r}") from exc
    if len(raw) > SHM_SYMBOL_WIRE_LIMIT:
        raise ValueError(
            f"SHM symbol {sym!r} ({len(raw)} UTF-8 bytes) exceeds "
            f"{SHM_SYMBOL_WIRE_LIMIT}-byte v3 wire limit"
        )
    return raw


def decode_shm_source(wire_source: str) -> str:
    """Decode an 8-byte v3 wire source token back to its canonical source string."""
    if not wire_source:
        return ""
    return WIRE_TO_CANONICAL_SOURCE_V3.get(wire_source, wire_source)


# Bounded caching for hot-path symbol/source byte-padding and string decoding
_FAST_ENCODE_CACHE: dict[tuple[str, str], tuple[bytes, bytes, int]] = {}
_BYTE_DECODE_CACHE: dict[bytes, str] = {}
_UTF8_DECODE_CACHE: dict[bytes, str] = {}


def _fast_encode_sym_src(symbol: str | None, source: str) -> tuple[bytes, bytes, int]:
    sym_key = "" if symbol is None else symbol
    pair = (sym_key, source)
    cached = _FAST_ENCODE_CACHE.get(pair)
    if cached is not None:
        return cached
    sym_b = encode_shm_symbol(symbol)
    src_b = (source or "").encode("ascii", errors="replace")
    trunc = 2 if len(src_b) > 8 else 0
    sym_bytes = sym_b.ljust(SHM_SYMBOL_WIRE_LIMIT, b"\x00")
    src_bytes = src_b[:8].ljust(8, b"\x00")
    res = (sym_bytes, src_bytes, trunc)
    if len(_FAST_ENCODE_CACHE) < 2048:
        _FAST_ENCODE_CACHE[pair] = res
    return res


def _fast_decode_utf8(b: bytes) -> str:
    cached = _UTF8_DECODE_CACHE.get(b)
    if cached is not None:
        return cached
    s = b.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    if len(_UTF8_DECODE_CACHE) < 2048:
        _UTF8_DECODE_CACHE[b] = s
    return s


def _fast_decode_ascii(b: bytes) -> str:
    cached = _BYTE_DECODE_CACHE.get(b)
    if cached is not None:
        return cached
    s = b.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    if len(_BYTE_DECODE_CACHE) < 2048:
        _BYTE_DECODE_CACHE[b] = s
    return s


@dataclass
class SHMOverrunStats:
    """Telemetry tracking slow reader buffer overruns and laps."""

    total_laps: int = 0
    skipped_ticks: int = 0
    last_lap_seq: int = 0
    last_lap_ts: float = 0.0
    watermark_warnings: int = 0
    watermark_events: int = 0


class SHMWriter:
    """
    High-throughput Shared Memory publisher (SHM v3).
    Allocates and maps a circular ring buffer, writing fixed-size binary slots
    with two-phase lock-free commit semantics and zero system calls on the hot path.
    """

    def __init__(self, name: str = "mdrap_feed", slot_count: int = DEFAULT_SLOT_COUNT):
        if not HAS_SHM:
            raise RuntimeError(
                "multiprocessing.shared_memory is not supported in this Python environment."
            )
        _check_arm_shm_safety()

        if slot_count < 2 or (slot_count & (slot_count - 1)) != 0:
            raise ValueError(f"slot_count must be a power of 2, got {slot_count}")

        self.raw_name = name
        self.name = normalize_shm_name(name)
        self.slot_count = slot_count
        self.mask = slot_count - 1
        self.total_size = HEADER_SIZE + (slot_count * SLOT_SIZE)
        self.shm: SharedMemory | None = None
        self.epoch_id = secrets.randbits(64) or 1
        self._head_seq = 0
        self._dropped_ticks = 0
        self._last_heartbeat = 0.0

        try:
            self.shm = SharedMemory(name=self.name, create=True, size=self.total_size)
        except FileExistsError:
            # Segment already exists across daemon restart.
            # Attach to existing segment, verify capacity, and clear ring with new epoch.
            self.shm = SharedMemory(name=self.name, create=False)
            if len(self.shm.buf) < self.total_size:
                self.shm.close()
                try:
                    self.shm.unlink()
                except Exception as exc:
                    logger.debug("[shm] Unlink existing segment error: %s", exc)
                self.shm = SharedMemory(
                    name=self.name, create=True, size=self.total_size
                )
            else:
                # Immediately poison epoch_id=0 and reset head=0 before clearing slots
                # so concurrently attaching readers see an in-progress reset rather than stale data.
                struct.pack_into("<QQ", self.shm.buf, 16, 0, 0)

        shm_file = f"/dev/shm/{self.name}"
        if os.path.exists(shm_file):
            try:
                os.chmod(shm_file, 0o600)
            except OSError as exc:
                logger.debug("[shm] chmod 0600 error: %s", exc)

        # Clear ring slots: write UNCOMMITTED into all commit_seq words
        for i in range(self.slot_count):
            struct.pack_into(
                "<Q", self.shm.buf, HEADER_SIZE + (i * SLOT_SIZE), UNCOMMITTED
            )

        # Initialize Cache Line 2 (Heartbeat Line) before exposing MAGIC/epoch_id
        # so attaching readers never observe a valid header with a zero heartbeat.
        pad44 = b"\x00" * 44
        now = time.time()
        HEADER_LINE2_STRUCT.pack_into(self.shm.buf, 64, now, 0, 0, pad44)
        self._last_heartbeat = now

        # Initialize Cache Line 1 (Writer Hot Line)
        pad32 = b"\x00" * 32
        HEADER_LINE1_STRUCT.pack_into(
            self.shm.buf,
            0,
            MAGIC,
            VERSION,
            SLOT_SIZE,
            self.slot_count,
            0,
            self.epoch_id,
            0,
            pad32,
        )
        self.watermark_pct = DEFAULT_SHM_WATERMARK_PCT
        self.watermark_slots = int(self.slot_count * self.watermark_pct)
        self._reader_cursors: dict[str | int, int] = {}
        self._last_known_read_seq = 0

    @property
    def _write_seq(self) -> int:
        return max(0, self._head_seq - 1)

    def set_watermark_flag(self, active: bool = True) -> None:
        """Set or clear the watermark warning bitflag in Cache Line 2."""
        if not self.shm:
            return
        flags = struct.unpack_from("<I", self.shm.buf, FLAGS_OFFSET)[0]
        if active:
            flags |= SHM_FLAG_WATERMARK_WARNING
        else:
            flags &= ~SHM_FLAG_WATERMARK_WARNING
        struct.pack_into("<I", self.shm.buf, FLAGS_OFFSET, flags)

    def is_watermark_warning_set(self) -> bool:
        """Check if the watermark warning bitflag is set in Cache Line 2."""
        if not self.shm:
            return False
        try:
            flags = struct.unpack_from("<I", self.shm.buf, FLAGS_OFFSET)[0]
            return bool(flags & SHM_FLAG_WATERMARK_WARNING)
        except Exception:
            return False

    def update_reader_seq(
        self, read_seq: int, reader_id: str | int = "default"
    ) -> None:
        """Update reader sequence and compute true slowest known reader sequence (min of active reader cursors)."""
        self._reader_cursors[reader_id] = max(
            self._reader_cursors.get(reader_id, 0), read_seq
        )
        oldest_consumer = (
            min(self._reader_cursors.values()) if self._reader_cursors else read_seq
        )
        self._last_known_read_seq = oldest_consumer
        occupancy = self._write_seq - oldest_consumer
        if occupancy >= self.watermark_slots:
            self.set_watermark_flag(True)
        elif occupancy < int(self.slot_count * (self.watermark_pct * 0.8)):
            self.set_watermark_flag(False)

    def update_heartbeat(self, dropped_ticks: int | None = None) -> None:
        """Update the publisher heartbeat timestamp in Cache Line 2."""
        if not self.shm:
            return
        if dropped_ticks is not None:
            self._dropped_ticks = dropped_ticks
        now = time.time()
        struct.pack_into("<dQ", self.shm.buf, 64, now, self._dropped_ticks)
        self._last_heartbeat = now

    def write_tick(
        self,
        seq: int,
        symbol: str,
        source: str,
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
    ) -> None:
        """
        Write a TICK event into the circular ring buffer slot with two-phase commit.
        """
        if not self.shm:
            return

        wire_source = encode_shm_source(source)
        canon_source = "" if source is None else source
        sym_bytes, src_bytes, trunc = _fast_encode_sym_src(symbol, wire_source)

        # Native acquire/release atomic publication fast path
        write_fn = (
            getattr(fastpath, "native_shm_write_tick", None) if fastpath else None
        )
        if write_fn is not None:
            try:
                if write_fn(
                    self.shm.buf,
                    self.slot_count,
                    seq,
                    symbol,
                    canon_source,
                    price,
                    size,
                    bid,
                    ask,
                    bid_size,
                    ask_size,
                    status,
                    is_crossed,
                    exchange_ts,
                    ingest_ts,
                    broadcast_ts,
                    engine_us,
                ):
                    self._head_seq = seq + 1
                    now = time.time()
                    if (seq & 0x1FF) == 0 or (now - self._last_heartbeat) >= 0.1:
                        self.update_heartbeat()
                    return
            except ValueError:
                raise
            except Exception as exc:
                logger.debug("[shm] Native write fallback: %s", exc)

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        st_code = STATUS_MAP_REV.get(status, 0)

        present = 0
        if price is not None:
            present |= SHM3_PRESENT_PRICE
        if size is not None:
            present |= SHM3_PRESENT_SIZE
        if bid is not None:
            present |= SHM3_PRESENT_BID
        if ask is not None:
            present |= SHM3_PRESENT_ASK
        if bid_size is not None:
            present |= SHM3_PRESENT_BSZ
        if ask_size is not None:
            present |= SHM3_PRESENT_ASZ

        # Phase 1: Invalidate slot so readers cannot observe torn state
        struct.pack_into("<Q", self.shm.buf, offset, UNCOMMITTED)

        # Write payload fields (offset + 8)
        PAYLOAD_STRUCT.pack_into(
            self.shm.buf,
            offset + 8,
            EVENT_TYPE_TICK,
            st_code,
            1 if is_crossed else 0,
            present,
            trunc,
            b"\x00" * 3,
            float(exchange_ts or 0.0),
            float(ingest_ts or 0.0),
            float(broadcast_ts or 0.0),
            float(engine_us or 0.0),
            0,
            float(price or 0.0),
            float(size or 0.0),
            float(bid or 0.0),
            float(ask or 0.0),
            float(bid_size or 0.0),
            float(ask_size or 0.0),
            sym_bytes,
            src_bytes,
            b"\x00" * 8,
        )

        # Phase 2: Commit slot sequence
        struct.pack_into("<Q", self.shm.buf, offset, seq)

        # Publish new head (next sequence = seq + 1)
        struct.pack_into("<Q", self.shm.buf, 24, seq + 1)
        self._head_seq = seq + 1

        # Periodic heartbeat update
        now = time.time()
        if (seq & 0x1FF) == 0 or (now - self._last_heartbeat) >= 0.1:
            self.update_heartbeat()

    def write_depth(
        self,
        seq: int,
        symbol: str,
        best_bid: float | None,
        best_ask: float | None,
        bid_size: float | None,
        ask_size: float | None,
        micro_price: float | None,
        ofi: float | None,
        is_crossed: bool,
        exchange_ts: float,
        ingest_ts: float,
        broadcast_ts: float,
        engine_us: float,
        status: str = "VALID",
    ) -> None:
        """
        Write a DEPTH event into the circular ring buffer slot with two-phase commit.
        """
        if not self.shm:
            return

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        st_code = STATUS_MAP_REV.get(status, 1)
        sym_bytes, src_bytes, trunc = _fast_encode_sym_src(symbol, "DEPTH")

        present = 0
        if micro_price is not None:
            present |= SHM3_PRESENT_PRICE
        if ofi is not None:
            present |= SHM3_PRESENT_SIZE
        if best_bid is not None:
            present |= SHM3_PRESENT_BID
        if best_ask is not None:
            present |= SHM3_PRESENT_ASK
        if bid_size is not None:
            present |= SHM3_PRESENT_BSZ
        if ask_size is not None:
            present |= SHM3_PRESENT_ASZ

        # Invalidate
        struct.pack_into("<Q", self.shm.buf, offset, UNCOMMITTED)

        # Write payload
        PAYLOAD_STRUCT.pack_into(
            self.shm.buf,
            offset + 8,
            EVENT_TYPE_DEPTH,
            st_code,
            1 if is_crossed else 0,
            present,
            trunc,
            b"\x00" * 3,
            float(exchange_ts or 0.0),
            float(ingest_ts or 0.0),
            float(broadcast_ts or 0.0),
            float(engine_us or 0.0),
            0,
            float(micro_price or 0.0),
            float(ofi or 0.0),
            float(best_bid or 0.0),
            float(best_ask or 0.0),
            float(bid_size or 0.0),
            float(ask_size or 0.0),
            sym_bytes,
            src_bytes,
            b"\x00" * 8,
        )

        # Commit & Publish
        struct.pack_into("<Q", self.shm.buf, offset, seq)
        struct.pack_into("<Q", self.shm.buf, 24, seq + 1)
        self._head_seq = seq + 1

        now = time.time()
        if (seq & 0x1FF) == 0 or (now - self._last_heartbeat) >= 0.1:
            self.update_heartbeat()

    def close(self) -> None:
        """Close and unlink shared memory segment."""
        if self.shm:
            try:
                # Invalidate epoch and heartbeat before closing/unlinking to immediately alert mapped readers
                if len(self.shm.buf) >= HEADER_SIZE:
                    try:
                        struct.pack_into("<Q", self.shm.buf, 16, 0)
                        struct.pack_into("<d", self.shm.buf, 64, 0.0)
                    except Exception:
                        pass
                name = getattr(self.shm, "_name", None)
                self.shm.close()
                if name:
                    try:
                        import _posixshmem

                        _posixshmem.shm_unlink(name)
                    except (ImportError, AttributeError, FileNotFoundError):
                        try:
                            self.shm.unlink()
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("[shm] Error closing/unlinking shm: %s", exc)
            self.shm = None


def _probe_active_epoch(name: str) -> int | None:
    """Probe active named shared memory segment to verify current epoch without leaking descriptors."""
    if not HAS_SHM:
        return None
    name = normalize_shm_name(name)
    try:
        try:
            probe = SharedMemory(name=name, create=False, track=False)
        except TypeError:
            probe = SharedMemory(name=name, create=False)
            _safe_unregister(probe._name)
        try:
            return struct.unpack_from("<Q", probe.buf, 16)[0]
        finally:
            probe.close()
    except Exception:
        return None


class SHMReader:
    """
    Sub-microsecond Shared Memory reader (SHM v3).
    Attaches to the memory-mapped ring buffer with zero kernel locks.
    Detects publisher restarts, validates commit sequences, and tracks overruns.
    """

    def __init__(self, name: str = "mdrap_feed"):
        if not HAS_SHM:
            raise RuntimeError(
                "multiprocessing.shared_memory is not supported in this Python environment."
            )
        _check_arm_shm_safety()

        self.raw_name = name
        self.name = normalize_shm_name(name)
        self.state = ReaderState.DETACHED
        try:
            self.shm: SharedMemory | None = SharedMemory(
                name=self.name, create=False, track=False
            )
        except TypeError:
            self.shm = SharedMemory(name=self.name, create=False)
            _safe_unregister(self.shm._name)

        buf_len = len(self.shm.buf)
        if buf_len < HEADER_SIZE + 2 * SLOT_SIZE:
            self.close()
            raise ValueError(f"SHM buffer too small: {buf_len} bytes")

        # Validate Line 1 Header
        magic, ver, slot_sz, slot_cnt, reserved, epoch_id, head_seq, _ = (
            HEADER_LINE1_STRUCT.unpack_from(self.shm.buf, 0)
        )
        if magic != MAGIC:
            self.close()
            raise ValueError(f"Invalid SHM magic: {magic} (expected {MAGIC})")
        if ver != VERSION:
            self.close()
            raise ValueError(f"Unsupported SHM version: {ver} (expected {VERSION})")
        if slot_sz != SLOT_SIZE:
            self.close()
            raise ValueError(f"Unexpected slot size: {slot_sz} (expected {SLOT_SIZE})")
        if slot_cnt < 2 or (slot_cnt & (slot_cnt - 1)) != 0:
            self.close()
            raise ValueError(f"Invalid slot count: {slot_cnt} (must be power of 2)")
        if len(self.shm.buf) < HEADER_SIZE + (slot_cnt * SLOT_SIZE):
            self.close()
            raise ValueError("Buffer truncated for declared slot count")
        if epoch_id == 0:
            self.close()
            raise ValueError("SHM segment epoch is 0 (uninitialized or closing)")

        self.slot_size = slot_sz
        self.slot_count = slot_cnt
        self.mask = slot_cnt - 1
        self.epoch_id = epoch_id
        self.watermark_pct = DEFAULT_SHM_WATERMARK_PCT
        self.watermark_slots = int(self.slot_count * self.watermark_pct)
        self.overrun_stats = SHMOverrunStats()
        self._last_epoch_probe = time.time()
        self._reattach_start_seq = 0
        self.state = ReaderState.ATTACHED

    def is_watermark_warning_set(self) -> bool:
        """Check if the watermark warning bitflag is set in Cache Line 2."""
        if not self.shm:
            return False
        try:
            flags = struct.unpack_from("<I", self.shm.buf, FLAGS_OFFSET)[0]
            return bool(flags & SHM_FLAG_WATERMARK_WARNING)
        except Exception:
            return False

    def check_watermark(self, current_seq: int | None = None) -> bool:
        """Check if writer watermark flag is raised or current reader seq has crossed watermark threshold."""
        if self.is_watermark_warning_set():
            return True
        if current_seq is not None and self.shm:
            head = struct.unpack_from("<Q", self.shm.buf, 24)[0]
            if (head - current_seq) >= self.watermark_slots:
                return True
        return False

    def is_writer_alive(self, max_stale_s: float = 4.0) -> bool:
        """Check if publisher heartbeat timestamp is recent."""
        if not self.shm:
            return False
        try:
            heartbeat_ts = struct.unpack_from("<d", self.shm.buf, 64)[0]
            return (time.time() - heartbeat_ts) < max_stale_s
        except Exception:
            return False

    def _reattach(self) -> bool:
        """Re-attach to named shared memory segment following a publisher restart or epoch change."""
        self.state = ReaderState.REATTACHING
        try:
            if self.shm:
                try:
                    self.shm.close()
                except Exception:
                    pass
                self.shm = None
            try:
                self.shm = SharedMemory(name=self.name, create=False, track=False)
            except TypeError:
                self.shm = SharedMemory(name=self.name, create=False)
                _safe_unregister(self.shm._name)

            buf_len = len(self.shm.buf)
            if buf_len < HEADER_SIZE + 2 * SLOT_SIZE:
                self.close()
                return False

            magic, ver, slot_sz, slot_cnt, reserved, epoch_id, head_seq, _ = (
                HEADER_LINE1_STRUCT.unpack_from(self.shm.buf, 0)
            )
            if (
                magic != MAGIC
                or ver != VERSION
                or slot_sz != SLOT_SIZE
                or slot_cnt < 2
                or (slot_cnt & (slot_cnt - 1)) != 0
                or len(self.shm.buf) < HEADER_SIZE + (slot_cnt * SLOT_SIZE)
                or epoch_id == 0
            ):
                self.close()
                self.state = ReaderState.INVALIDATED
                return False

            self.slot_size = slot_sz
            self.slot_count = slot_cnt
            self.mask = slot_cnt - 1
            self.epoch_id = epoch_id
            self.watermark_pct = DEFAULT_SHM_WATERMARK_PCT
            self.watermark_slots = int(self.slot_count * self.watermark_pct)
            self._last_epoch_probe = time.time()
            self._reattach_start_seq = self.earliest_available_seq(record_overrun=True)
            self.state = ReaderState.ATTACHED
            return True
        except Exception as exc:
            logger.debug("[shm] Failed to reattach to %s: %s", self.name, exc)
            self.state = ReaderState.INVALIDATED
            return False

    def _find_first_committed_seq(self, start: int, head: int) -> int:
        """Advance past leading UNCOMMITTED slots (e.g. when writer started at seq=1)."""
        if not self.shm or head <= start:
            return start
        limit = min(head, start + self.slot_count)
        seq = start
        while seq < limit:
            slot_idx = seq & self.mask
            offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)
            c = struct.unpack_from("<Q", self.shm.buf, offset)[0]
            if c == seq:
                return seq
            if c != UNCOMMITTED:
                return seq
            seq += 1
        return start

    def earliest_available_seq(self, record_overrun: bool = False) -> int:
        """
        Return the earliest retained committed sequence in the ring buffer.

        - When head == 0: returns 0 (no ticks published yet).
        - When head > 0: computes oldest_ring_seq = max(0, head - slot_count)
          and advances past leading UNCOMMITTED slots (e.g. writer starting at seq=1).
        - When record_overrun=True and head > slot_count: accounts for the
          overwritten unavailable sequence range in self.overrun_stats.
        """
        if not self.shm:
            return 0
        head = struct.unpack_from("<Q", self.shm.buf, 24)[0]
        if head == 0:
            return 0
        oldest_ring_seq = max(0, head - self.slot_count)
        if oldest_ring_seq > 0 and record_overrun:
            self.overrun_stats.total_laps += 1
            self.overrun_stats.skipped_ticks += oldest_ring_seq
            self.overrun_stats.last_lap_seq = oldest_ring_seq - 1
            self.overrun_stats.last_lap_ts = time.time()
        return self._find_first_committed_seq(oldest_ring_seq, head)

    def check_epoch_valid(self, force_probe: bool = False) -> bool:
        """
        Verify that the writer epoch has not changed (i.e. daemon has not restarted).
        Fast in-memory read from offset 16 of mapped buffer is zero syscalls.
        External named segment probing is performed when force_probe=True, when
        the mapped POSIX inode on Linux is unlinked (st_nlink == 0), or when heartbeat is stale.
        """
        if not self.shm or self.state == ReaderState.DETACHED:
            return False
        try:
            # POSIX unlinked segment detection on Linux only (Darwin fstat always returns st_nlink == 0 for shm_open)
            if (
                platform.system() == "Linux"
                and hasattr(self.shm, "_fd")
                and isinstance(self.shm._fd, int)
                and self.shm._fd >= 0
            ):
                try:
                    if os.fstat(self.shm._fd).st_nlink == 0:
                        self.state = ReaderState.INVALIDATED
                        return False
                except OSError:
                    self.state = ReaderState.INVALIDATED
                    return False

            current_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
            if current_epoch == 0 or current_epoch != self.epoch_id:
                self.state = ReaderState.INVALIDATED
                return False

            should_probe = force_probe
            if not should_probe and not self.is_writer_alive(max_stale_s=2.0):
                should_probe = True

            if should_probe:
                now = time.time()
                if (now - self._last_epoch_probe) >= 0.05 or force_probe:
                    self._last_epoch_probe = now
                    active_epoch = _probe_active_epoch(self.name)
                    if (
                        active_epoch is None
                        or active_epoch == 0
                        or active_epoch != self.epoch_id
                    ):
                        self.state = ReaderState.INVALIDATED
                        return False

            self.state = ReaderState.ATTACHED
            return True
        except Exception:
            self.state = ReaderState.INVALIDATED
            return False

    def read_latest_seq(self) -> int:
        """Read current latest published sequence number from Header Cache Line 1."""
        if not self.shm:
            return 0
        head = struct.unpack_from("<Q", self.shm.buf, 24)[0]
        return max(0, head - 1) if head > 0 else 0

    def read_slot(self, seq: int) -> dict | None:
        """
        Read and unpack a specific sequence slot with two-phase commit verification.
        Returns None if slot write is in progress, overwritten, or not yet committed.
        """
        if not self.shm:
            return None

        head = struct.unpack_from("<Q", self.shm.buf, 24)[0]

        # Overrun detection: publisher has lapped the reader
        if head > 0 and head > seq and (head - seq) > self.slot_count:
            self.overrun_stats.total_laps += 1
            self.overrun_stats.last_lap_seq = seq
            self.overrun_stats.last_lap_ts = time.time()
            return None

        if head > seq:
            lag = head - seq
            if lag >= self.watermark_slots or self.is_watermark_warning_set():
                self.overrun_stats.watermark_warnings += 1
                self.overrun_stats.watermark_events += 1

        # Native acquire/release atomic slot read fast path
        read_fn = getattr(fastpath, "native_shm_read_slot", None) if fastpath else None
        if read_fn is not None:
            try:
                native_item = read_fn(self.shm.buf, self.slot_count, seq)
                if native_item is not None:
                    if "source" in native_item:
                        native_item["source"] = decode_shm_source(native_item["source"])
                    return native_item
            except Exception as exc:
                logger.debug("[shm] Native slot read fallback: %s", exc)

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        # Pre-check commit_seq
        c1 = struct.unpack_from("<Q", self.shm.buf, offset)[0]
        if c1 != seq:
            return None

        # Unpack payload
        payload = PAYLOAD_STRUCT.unpack_from(self.shm.buf, offset + 8)

        # Post-check commit_seq (seqlock double check for torn read detection)
        c2 = struct.unpack_from("<Q", self.shm.buf, offset)[0]
        if c2 != c1:
            return None

        (
            ev_type_code,
            st_code,
            crossed,
            present,
            trunc,
            pad1,
            ex_ts,
            in_ts,
            bc_ts,
            eng_us,
            pad2,
            p,
            sz,
            bid_val,
            ask_val,
            bid_sz,
            ask_sz,
            sym_bytes,
            src_bytes,
            pad3,
        ) = payload

        sym = _fast_decode_utf8(sym_bytes)
        src = decode_shm_source(_fast_decode_ascii(src_bytes))

        price = p if (present & SHM3_PRESENT_PRICE) else None
        size = sz if (present & SHM3_PRESENT_SIZE) else None
        bid = bid_val if (present & SHM3_PRESENT_BID) else None
        ask = ask_val if (present & SHM3_PRESENT_ASK) else None
        bid_size = bid_sz if (present & SHM3_PRESENT_BSZ) else None
        ask_size = ask_sz if (present & SHM3_PRESENT_ASZ) else None

        status_str = STATUS_MAP_FWD.get(st_code, "UNKNOWN")

        if ev_type_code == EVENT_TYPE_DEPTH:
            return {
                "type": "DEPTH",
                "seq": c1,
                "sym": sym,
                "micro_price": price,
                "ofi": size,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "bids": [[bid, bid_size, "AGG"]] if bid is not None else [],
                "asks": [[ask, ask_size, "AGG"]] if ask is not None else [],
                "is_crossed": bool(crossed),
                "status": status_str,
                "exchange_ts": ex_ts,
                "ingest_ts": in_ts,
                "broadcast_ts": bc_ts,
                "engine_us": eng_us,
            }
        else:
            return {
                "type": "TICK",
                "seq": c1,
                "sym": sym,
                "price": price,
                "size": size,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "source": src,
                "status": status_str,
                "is_crossed": bool(crossed),
                "exchange_ts": ex_ts,
                "ingest_ts": in_ts,
                "broadcast_ts": bc_ts,
                "engine_us": eng_us,
            }

    def stream(
        self,
        start_seq: int | None = None,
        timeout: float | None = None,
        max_events: int | None = None,
        writer_dead_timeout: float | None = None,
    ) -> Generator[dict, None, None]:
        """
        Stream market data frames directly from shared memory with sub-microsecond polling.
        Automatically catches up on overruns without blocking.
        Detects publisher crash and deadlock when writer_dead_timeout is specified.
        """
        curr_seq = start_seq if start_seq is not None else self.read_latest_seq()
        count = 0
        t_start = time.time()
        spin_count = 0

        # Initial publisher liveness and epoch validation before streaming
        if writer_dead_timeout is not None and not self.is_writer_alive(
            max_stale_s=writer_dead_timeout
        ):
            logger.warning(
                "[shm] Publisher heartbeat stale > %.2fs on stream startup",
                writer_dead_timeout,
            )
            yield {
                "type": "PUBLISHER_DEAD",
                "reason": f"Heartbeat stale > {writer_dead_timeout}s on startup",
            }
            return

        if not self.check_epoch_valid(force_probe=False):
            old_epoch = self.epoch_id
            if self._reattach() and self.epoch_id != old_epoch and self.epoch_id != 0:
                curr_seq = self._reattach_start_seq
                yield {
                    "type": "EPOCH_CHANGE",
                    "old_epoch": old_epoch,
                    "new_epoch": self.epoch_id,
                }
            elif not self.check_epoch_valid(force_probe=False):
                return

        while True:
            if not self.shm or self.state == ReaderState.DETACHED:
                return

            # Fast in-memory epoch check (nanoseconds, zero syscalls)
            try:
                current_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
                if current_epoch != 0 and current_epoch != self.epoch_id:
                    magic, ver, slot_sz, slot_cnt, _, _, _, _ = (
                        HEADER_LINE1_STRUCT.unpack_from(self.shm.buf, 0)
                    )
                    if (
                        magic == MAGIC
                        and ver == VERSION
                        and slot_sz == SLOT_SIZE
                        and slot_cnt >= 2
                        and (slot_cnt & (slot_cnt - 1)) == 0
                    ):
                        old_epoch = self.epoch_id
                        self.slot_size = slot_sz
                        self.slot_count = slot_cnt
                        self.mask = slot_cnt - 1
                        self.epoch_id = current_epoch
                        curr_seq = self.earliest_available_seq(record_overrun=True)
                        yield {
                            "type": "EPOCH_CHANGE",
                            "old_epoch": old_epoch,
                            "new_epoch": self.epoch_id,
                        }
                        continue
            except Exception as exc:
                logger.debug("[shm] Error reading current epoch: %s", exc)

            head = struct.unpack_from("<Q", self.shm.buf, 24)[0]
            # Overrun check
            if head > 0 and head > curr_seq and (head - curr_seq) > self.slot_count:
                skipped = (head - self.slot_count) - curr_seq
                self.overrun_stats.total_laps += 1
                self.overrun_stats.skipped_ticks += max(0, skipped)
                self.overrun_stats.last_lap_seq = (head - self.slot_count) - 1
                self.overrun_stats.last_lap_ts = time.time()
                curr_seq = head - self.slot_count

            item = self.read_slot(curr_seq)
            if item:
                item["recv_ts"] = time.time()
                yield item
                count += 1
                curr_seq += 1
                t_start = time.time()
                spin_count = 0
                if max_events and count >= max_events:
                    return
                continue

            # If starting at seq=0 while writer started at seq=1 (slot 0 never committed),
            # advance to the first committed slot so initial ticks are never missed.
            if curr_seq == 0 and head > 1:
                resolved = self._find_first_committed_seq(curr_seq, head)
                if resolved > curr_seq:
                    curr_seq = resolved
                    continue

            # When caught up with writer or spinning, verify writer liveness & external epoch validity
            if spin_count == 0 or (spin_count & 0x3F) == 0:
                if writer_dead_timeout is not None and not self.is_writer_alive(
                    max_stale_s=writer_dead_timeout
                ):
                    logger.warning(
                        "[shm] Publisher heartbeat stale > %.2fs during spin, exiting stream",
                        writer_dead_timeout,
                    )
                    yield {
                        "type": "PUBLISHER_DEAD",
                        "reason": f"Heartbeat stale > {writer_dead_timeout}s",
                    }
                    return

                if not self.check_epoch_valid(force_probe=False):
                    old_epoch = self.epoch_id
                    if (
                        self._reattach()
                        and self.epoch_id != old_epoch
                        and self.epoch_id != 0
                    ):
                        curr_seq = self._reattach_start_seq
                        yield {
                            "type": "EPOCH_CHANGE",
                            "old_epoch": old_epoch,
                            "new_epoch": self.epoch_id,
                        }
                        continue
                    elif not self.check_epoch_valid(force_probe=False):
                        return

            # Timeout check
            if timeout is not None and (time.time() - t_start) > timeout:
                return

            # Spin-then-sleep (M14)
            spin_count += 1
            if spin_count < 2000:
                pass
            elif spin_count < 5000:
                time.sleep(0.00005)  # 50 µs
            else:
                time.sleep(0.001)  # 1 ms

    def read_batch(self, max_n: int = 64) -> list[dict]:
        """Unpack up to max_n available slots in one call."""
        results = []
        curr = self.read_latest_seq()
        for s in range(curr, curr + max_n):
            item = self.read_slot(s)
            if not item:
                break
            results.append(item)
        return results

    read_tick = read_slot  # Backward-compatible and protocol-consistent alias

    def close(self) -> None:
        """Close shared memory handle."""
        self.state = ReaderState.DETACHED
        if self.shm:
            try:
                self.shm.close()
            except Exception as exc:
                logger.debug("[shm] Error closing reader shm: %s", exc)
            self.shm = None
