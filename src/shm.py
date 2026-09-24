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
import os
import secrets
import struct
import time
from dataclasses import dataclass

try:
    from multiprocessing.shared_memory import SharedMemory

    HAS_SHM = True
except ImportError:
    HAS_SHM = False

__stability__ = "stable"

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

# Bounded caching for hot-path ASCII byte-padding and string decoding
_FAST_ENCODE_CACHE: dict[tuple[str, str], tuple[bytes, bytes, int]] = {}
_BYTE_DECODE_CACHE: dict[bytes, str] = {}


def _fast_encode_sym_src(symbol: str, source: str) -> tuple[bytes, bytes, int]:
    pair = (symbol, source)
    cached = _FAST_ENCODE_CACHE.get(pair)
    if cached is not None:
        return cached
    sym_b = (symbol or "").encode("ascii", errors="replace")
    src_b = (source or "").encode("ascii", errors="replace")
    trunc = (1 if len(sym_b) > 16 else 0) | (2 if len(src_b) > 8 else 0)
    sym_bytes = sym_b[:16].ljust(16, b"\x00")
    src_bytes = src_b[:8].ljust(8, b"\x00")
    res = (sym_bytes, src_bytes, trunc)
    if len(_FAST_ENCODE_CACHE) < 2048:
        _FAST_ENCODE_CACHE[pair] = res
    return res


def _fast_decode_ascii(b: bytes) -> str:
    cached = _BYTE_DECODE_CACHE.get(b)
    if cached is not None:
        return cached
    s = b.rstrip(b"\x00").decode("ascii", errors="replace")
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

        if slot_count < 2 or (slot_count & (slot_count - 1)) != 0:
            raise ValueError(f"slot_count must be a power of 2, got {slot_count}")

        self.name = name
        self.slot_count = slot_count
        self.mask = slot_count - 1
        self.total_size = HEADER_SIZE + (slot_count * SLOT_SIZE)
        self.shm: SharedMemory | None = None
        self.epoch_id = secrets.randbits(64)
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
                except Exception:
                    pass
                self.shm = SharedMemory(
                    name=self.name, create=True, size=self.total_size
                )

        # Clear ring slots: write UNCOMMITTED into all commit_seq words
        for i in range(self.slot_count):
            struct.pack_into(
                "<Q", self.shm.buf, HEADER_SIZE + (i * SLOT_SIZE), UNCOMMITTED
            )

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

        # Initialize Cache Line 2 (Heartbeat Line)
        pad44 = b"\x00" * 44
        now = time.time()
        HEADER_LINE2_STRUCT.pack_into(self.shm.buf, 64, now, 0, 0, pad44)
        self._last_heartbeat = now
        self.watermark_pct = DEFAULT_SHM_WATERMARK_PCT
        self.watermark_slots = int(self.slot_count * self.watermark_pct)
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

    def update_reader_seq(self, read_seq: int) -> None:
        """Update slowest known reader sequence to evaluate ring buffer occupancy."""
        self._last_known_read_seq = max(self._last_known_read_seq, read_seq)
        occupancy = self._write_seq - self._last_known_read_seq
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
        price: float | None,
        size: float | None,
        bid: float | None,
        ask: float | None,
        bid_size: float | None,
        ask_size: float | None,
        status: str,
        is_crossed: bool,
        exchange_ts: float,
        ingest_ts: float,
        broadcast_ts: float,
        engine_us: float,
    ) -> None:
        """
        Write a TICK event into the circular ring buffer slot with two-phase commit.
        """
        if not self.shm:
            return

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        st_code = STATUS_MAP_REV.get(status, 0)
        sym_bytes, src_bytes, trunc = _fast_encode_sym_src(symbol, source)

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
                self.shm.close()
                self.shm.unlink()
            except Exception:
                pass
            self.shm = None


def _probe_active_epoch(name: str) -> int | None:
    """Probe active named shared memory segment to verify current epoch without leaking descriptors."""
    if not HAS_SHM:
        return None
    try:
        try:
            probe = SharedMemory(name=name, create=False, track=False)
        except TypeError:
            probe = SharedMemory(name=name, create=False)
            try:
                from multiprocessing import resource_tracker

                resource_tracker.unregister(probe._name, "shared_memory")
            except Exception:
                pass
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

        self.name = name
        try:
            self.shm: SharedMemory | None = SharedMemory(
                name=self.name, create=False, track=False
            )
        except TypeError:
            self.shm = SharedMemory(name=self.name, create=False)
            try:
                from multiprocessing import resource_tracker

                resource_tracker.unregister(self.shm._name, "shared_memory")
            except Exception:
                pass

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

        self.slot_size = slot_sz
        self.slot_count = slot_cnt
        self.mask = slot_cnt - 1
        self.epoch_id = epoch_id
        self.watermark_pct = DEFAULT_SHM_WATERMARK_PCT
        self.watermark_slots = int(self.slot_count * self.watermark_pct)
        self.overrun_stats = SHMOverrunStats()

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

    def check_epoch_valid(self) -> bool:
        """
        Verify that the writer epoch has not changed (i.e. daemon has not restarted).
        On POSIX, an unlinked segment remains mapped by old readers; probing the named
        segment ensures detection when a new writer creates a replacement segment.
        """
        if not self.shm:
            return False
        try:
            current_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
            if current_epoch != self.epoch_id:
                return False
            active_epoch = _probe_active_epoch(self.name)
            if active_epoch is None or active_epoch != self.epoch_id:
                return False
            return True
        except Exception:
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
        if head == 0 or seq >= head:
            return None  # Future sequence or nothing published

        lag = head - seq
        if lag >= self.watermark_slots or self.is_watermark_warning_set():
            self.overrun_stats.watermark_warnings += 1
            self.overrun_stats.watermark_events += 1

        # Overrun detection: publisher has lapped the reader
        if lag > self.slot_count:
            self.overrun_stats.total_laps += 1
            self.overrun_stats.last_lap_seq = seq
            self.overrun_stats.last_lap_ts = time.time()
            return None

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

        sym = _fast_decode_ascii(sym_bytes)
        src = _fast_decode_ascii(src_bytes)

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
    ) -> Generator[dict, None, None]:
        """
        Stream market data frames directly from shared memory with sub-microsecond polling.
        Automatically catches up on overruns without blocking.
        """
        curr_seq = start_seq if start_seq is not None else self.read_latest_seq()
        count = 0
        t_start = time.time()
        spin_count = 0

        # Initial epoch validation before streaming
        if not self.check_epoch_valid():
            try:
                new_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
                self.epoch_id = new_epoch
            except Exception:
                pass
            curr_seq = 0
            yield {"type": "EPOCH_CHANGE"}

        while True:
            # Fast in-memory epoch check (nanoseconds, zero syscalls)
            try:
                current_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
                if current_epoch != self.epoch_id:
                    self.epoch_id = current_epoch
                    curr_seq = 0
                    yield {"type": "EPOCH_CHANGE"}
                    continue
            except Exception:
                pass

            head = struct.unpack_from("<Q", self.shm.buf, 24)[0]
            if curr_seq < head:
                # Overrun check
                if head - curr_seq > self.slot_count:
                    skipped = (head - self.slot_count) - curr_seq
                    self.overrun_stats.skipped_ticks += max(0, skipped)
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

            # When caught up with writer or spinning, verify external epoch validity
            if spin_count == 0 or (spin_count & 0xFF) == 0:
                if not self.check_epoch_valid():
                    try:
                        new_epoch = struct.unpack_from("<Q", self.shm.buf, 16)[0]
                        self.epoch_id = new_epoch
                    except Exception:
                        pass
                    curr_seq = 0
                    yield {"type": "EPOCH_CHANGE"}
                    continue

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

    def close(self) -> None:
        """Close shared memory handle."""
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
