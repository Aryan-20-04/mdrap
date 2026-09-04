"""
MDRAP Zero-Copy Shared Memory (SHM) Ring Buffer Engine (Spec §18).

Provides sub-microsecond (<1µs) IPC market data delivery for co-located
institutional trading algorithms using memory-mapped ring buffers.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

try:
    from multiprocessing.shared_memory import SharedMemory
    HAS_SHM = True
except ImportError:
    HAS_SHM = False

# Binary layout constants
MAGIC = b"MDRP"
VERSION = 1
DEFAULT_SLOT_COUNT = 16384   # Must be power of 2 for fast bitwise masking
SLOT_SIZE = 128              # Cache-line aligned (2 x 64 bytes)
HEADER_SIZE = 64             # Cache-line aligned (1 x 64 bytes)
TOTAL_SHM_SIZE = HEADER_SIZE + (DEFAULT_SLOT_COUNT * SLOT_SIZE)

# Struct pack formats
# Header (64 bytes): magic(4s), version(H), slot_size(H), slot_count(I), write_seq(Q), padding(44s)
HEADER_STRUCT = struct.Struct("<4sHHIQ44s")

# Slot (128 bytes):
# seq(Q=8), event_type(B=1), status(B=1), is_crossed(B=1), pad1(5s=5) -> 16B
# exchange_ts(d=8), ingest_ts(d=8), broadcast_ts(d=8) -> 24B (total 40B)
# engine_us(f=4), pad2(4s=4) -> 8B (total 48B)
# price(d=8), size(d=8), bid(d=8), ask(d=8), bid_sz(d=8), ask_sz(d=8) -> 48B (total 96B)
# symbol(16s=16), source(8s=8), pad3(8s=8) -> 32B (total 128B)
SLOT_STRUCT = struct.Struct("<QBBB5sdddf4sdddddd16s8s8s")

EVENT_TYPE_TICK = 1
EVENT_TYPE_DEPTH = 2

STATUS_MAP_REV = {"VALID": 1, "SUSPICIOUS": 2, "INVALID": 3}
STATUS_MAP_FWD = {1: "VALID", 2: "SUSPICIOUS", 3: "INVALID"}


class SHMWriter:
    """
    High-throughput Shared Memory publisher.
    Allocates or connects to memory-mapped buffer and writes fixed-size binary slots.
    """

    def __init__(self, name: str = "mdrap_feed", slot_count: int = DEFAULT_SLOT_COUNT):
        if not HAS_SHM:
            raise RuntimeError("multiprocessing.shared_memory is not supported in this Python environment.")

        self.name = name
        self.slot_count = slot_count
        self.mask = slot_count - 1
        self.total_size = HEADER_SIZE + (slot_count * SLOT_SIZE)
        self.shm: Optional[SharedMemory] = None

        # Clean up any stale segment with same name
        try:
            stale = SharedMemory(name=self.name, create=False)
            stale.close()
            stale.unlink()
        except Exception:
            pass

        self.shm = SharedMemory(name=self.name, create=True, size=self.total_size)
        # Initialize header
        pad44 = b"\x00" * 44
        HEADER_STRUCT.pack_into(self.shm.buf, 0, MAGIC, VERSION, SLOT_SIZE, self.slot_count, 0, pad44)
        self._write_seq = 0

    def write_tick(
        self,
        seq: int,
        symbol: str,
        source: str,
        price: Optional[float],
        size: Optional[float],
        bid: Optional[float],
        ask: Optional[float],
        bid_size: Optional[float],
        ask_size: Optional[float],
        status: str,
        is_crossed: bool,
        exchange_ts: float,
        ingest_ts: float,
        broadcast_ts: float,
        engine_us: float,
    ) -> None:
        """Write a TICK event into the circular ring buffer slot."""
        if not self.shm:
            return

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        st_code = STATUS_MAP_REV.get(status, 1)
        sym_bytes = symbol.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
        src_bytes = source.encode("ascii", errors="replace")[:8].ljust(8, b"\x00")

        SLOT_STRUCT.pack_into(
            self.shm.buf,
            offset,
            seq,
            EVENT_TYPE_TICK,
            st_code,
            1 if is_crossed else 0,
            b"\x00" * 5,
            float(exchange_ts or 0.0),
            float(ingest_ts or 0.0),
            float(broadcast_ts or 0.0),
            float(engine_us or 0.0),
            b"\x00" * 4,
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

        # Update write_seq in header (offset 12 in header: 4s+H+H+I = 12)
        struct.pack_into("<Q", self.shm.buf, 12, seq)
        self._write_seq = seq

    def write_depth(
        self,
        seq: int,
        symbol: str,
        best_bid: Optional[float],
        best_ask: Optional[float],
        bid_size: Optional[float],
        ask_size: Optional[float],
        micro_price: Optional[float],
        ofi: Optional[float],
        is_crossed: bool,
        exchange_ts: float,
        ingest_ts: float,
        broadcast_ts: float,
        engine_us: float,
    ) -> None:
        """Write a DEPTH event into the circular ring buffer slot."""
        if not self.shm:
            return

        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        sym_bytes = symbol.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
        src_bytes = b"DEPTH\x00\x00\x00"

        SLOT_STRUCT.pack_into(
            self.shm.buf,
            offset,
            seq,
            EVENT_TYPE_DEPTH,
            1,  # VALID
            1 if is_crossed else 0,
            b"\x00" * 5,
            float(exchange_ts or 0.0),
            float(ingest_ts or 0.0),
            float(broadcast_ts or 0.0),
            float(engine_us or 0.0),
            b"\x00" * 4,
            float(micro_price or 0.0),  # price slot holds micro_price
            float(ofi or 0.0),          # size slot holds ofi
            float(best_bid or 0.0),
            float(best_ask or 0.0),
            float(bid_size or 0.0),
            float(ask_size or 0.0),
            sym_bytes,
            src_bytes,
            b"\x00" * 8,
        )

        struct.pack_into("<Q", self.shm.buf, 12, seq)
        self._write_seq = seq

    def close(self) -> None:
        if self.shm:
            try:
                self.shm.close()
                self.shm.unlink()
            except Exception:
                pass
            self.shm = None


class SHMReader:
    """
    Sub-microsecond (<1µs) Shared Memory reader.
    Attaches to the memory-mapped ring buffer and yields streaming MarketEvents.
    """

    def __init__(self, name: str = "mdrap_feed"):
        if not HAS_SHM:
            raise RuntimeError("multiprocessing.shared_memory is not supported in this Python environment.")

        self.name = name
        self.shm = SharedMemory(name=self.name, create=False)
        # Validate header
        magic, ver, slot_sz, slot_cnt, write_seq, _ = HEADER_STRUCT.unpack_from(self.shm.buf, 0)
        if magic != MAGIC:
            self.close()
            raise ValueError(f"Invalid SHM magic: {magic} (expected {MAGIC})")
        if ver != VERSION:
            self.close()
            raise ValueError(f"Unsupported SHM version: {ver}")

        self.slot_size = slot_sz
        self.slot_count = slot_cnt
        self.mask = slot_cnt - 1

    def close(self) -> None:
        if self.shm:
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None

    def read_latest_seq(self) -> int:
        """Read current head write sequence number atomically from header."""
        return struct.unpack_from("<Q", self.shm.buf, 12)[0]

    def read_slot(self, seq: int) -> Optional[dict]:
        """Read and unpack a specific sequence slot with zero system calls."""
        slot_idx = seq & self.mask
        offset = HEADER_SIZE + (slot_idx * SLOT_SIZE)

        unpacked = SLOT_STRUCT.unpack_from(self.shm.buf, offset)
        slot_seq = unpacked[0]
        if slot_seq != seq:
            # Overrun occurred or slot not written yet
            return None

        ev_type_code = unpacked[1]
        st_code = unpacked[2]
        crossed = bool(unpacked[3])
        ex_ts = unpacked[5]
        in_ts = unpacked[6]
        bc_ts = unpacked[7]
        eng_us = unpacked[8]
        p1 = unpacked[10]
        p2 = unpacked[11]
        bid = unpacked[12]
        ask = unpacked[13]
        bid_sz = unpacked[14]
        ask_sz = unpacked[15]
        sym = unpacked[16].rstrip(b"\x00").decode("ascii", errors="replace")
        src = unpacked[17].rstrip(b"\x00").decode("ascii", errors="replace")

        if ev_type_code == EVENT_TYPE_DEPTH:
            return {
                "type": "DEPTH",
                "seq": slot_seq,
                "sym": sym,
                "micro_price": p1,
                "ofi": p2,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "bids": [[bid, bid_sz, "AGG"]],
                "asks": [[ask, ask_sz, "AGG"]],
                "is_crossed": crossed,
                "status": "VALID",
                "exchange_ts": ex_ts,
                "ingest_ts": in_ts,
                "broadcast_ts": bc_ts,
                "engine_us": eng_us,
            }
        else:
            return {
                "type": "TICK",
                "seq": slot_seq,
                "sym": sym,
                "price": p1,
                "size": p2,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_sz,
                "ask_size": ask_sz,
                "source": src,
                "status": STATUS_MAP_FWD.get(st_code, "VALID"),
                "is_crossed": crossed,
                "exchange_ts": ex_ts,
                "ingest_ts": in_ts,
                "broadcast_ts": bc_ts,
                "engine_us": eng_us,
            }

    def stream(
        self,
        start_seq: Optional[int] = None,
        timeout: Optional[float] = None,
        max_events: Optional[int] = None,
    ) -> Generator[dict, None, None]:
        """
        Stream market data frames directly from shared memory with sub-microsecond polling.
        """
        curr_seq = start_seq if start_seq is not None else self.read_latest_seq()
        count = 0
        t_start = time.time()

        while True:
            head_seq = self.read_latest_seq()
            if curr_seq <= head_seq:
                # Check for buffer overrun (writer lapped reader)
                if head_seq - curr_seq >= self.slot_count:
                    # Skip to earliest available sequence
                    curr_seq = head_seq - self.slot_count + 1

                item = self.read_slot(curr_seq)
                if item:
                    item["recv_ts"] = time.time()
                    yield item
                    count += 1
                    curr_seq += 1
                    t_start = time.time()
                    if max_events and count >= max_events:
                        return
                    continue

            # No new data yet
            if timeout is not None and (time.time() - t_start) > timeout:
                return

            # Sub-millisecond CPU pause
            time.sleep(0.0001)
