"""
MDRAP Binary Journal (.dbn / AOF): Ultra-High-Throughput Append-Only Persistence.

Implements an append-only memory-mapped binary transaction log designed for
sub-microsecond tick durability, zero serialization overhead, and instant replay.
Uses 128-byte fixed-size binary records matching the SHM Slot V3 architecture (Spec §5, §26).
"""
from __future__ import annotations

import mmap
import os
import struct
import time
from typing import Any, Generator

from shm import (
    SLOT_SIZE,
    SLOT_STRUCT,
    HEADER_SIZE,
    STATUS_MAP_FWD,
    STATUS_MAP_REV,
    SHM3_PRESENT_PRICE,
    SHM3_PRESENT_SIZE,
    SHM3_PRESENT_BID,
    SHM3_PRESENT_ASK,
    SHM3_PRESENT_BSZ,
    SHM3_PRESENT_ASZ,
    EVENT_TYPE_TICK,
    EVENT_TYPE_DEPTH,
)

JOURNAL_MAGIC = b"MDBJ"
JOURNAL_VERSION = 1
JOURNAL_HEADER_SIZE = 128
DEFAULT_INITIAL_RECORDS = 65536  # 8 MB initial preallocation
GROWTH_FACTOR = 2

# Journal Header: magic[4], u16 version, u16 record_size, u64 epoch, u64 record_count,
#                u64 first_seq, u64 last_seq, double created_ts, double updated_ts, pad[72]
JOURNAL_HDR_STRUCT = struct.Struct("<4sHHQQQQdd72s")
assert JOURNAL_HDR_STRUCT.size == JOURNAL_HEADER_SIZE


class BinaryJournal:
    """
    Append-only memory-mapped binary journal writer.
    Writes fixed 128-byte market event frames at memory bus speed.
    """

    def __init__(
        self,
        filepath: str,
        initial_records: int = DEFAULT_INITIAL_RECORDS,
        epoch: int | None = None,
    ):
        self.filepath = filepath
        self.initial_records = max(1024, initial_records)
        self.epoch = epoch if epoch is not None else int(time.time_ns())
        self._fd: int | None = None
        self._mm: mmap.mmap | None = None
        self._capacity_records: int = 0
        self._record_count: int = 0
        self._first_seq: int = 0
        self._last_seq: int = 0
        self._created_ts: float = time.time()
        self._closed: bool = False

        self._init_journal()

    def _init_journal(self) -> None:
        file_exists = os.path.exists(self.filepath) and os.path.getsize(self.filepath) >= JOURNAL_HEADER_SIZE

        if file_exists:
            # Reopen existing journal
            self._fd = os.open(self.filepath, os.O_RDWR | getattr(os, "O_BINARY", 0))
            file_size = os.path.getsize(self.filepath)
            self._mm = mmap.mmap(self._fd, 0, access=mmap.ACCESS_WRITE)
            (
                magic,
                ver,
                rec_sz,
                epoch,
                count,
                first_seq,
                last_seq,
                created_ts,
                updated_ts,
                _,
            ) = JOURNAL_HDR_STRUCT.unpack_from(self._mm, 0)

            if magic != JOURNAL_MAGIC:
                self.close()
                raise ValueError(f"Invalid journal magic: {magic!r}")
            if ver != JOURNAL_VERSION:
                self.close()
                raise ValueError(f"Unsupported journal version: {ver}")
            if rec_sz != SLOT_SIZE:
                self.close()
                raise ValueError(f"Incompatible journal record size: {rec_sz} != {SLOT_SIZE}")

            self.epoch = epoch
            self._record_count = count
            self._first_seq = first_seq
            self._last_seq = last_seq
            self._created_ts = created_ts

            rem = (file_size - JOURNAL_HEADER_SIZE) % SLOT_SIZE
            if rem != 0:
                clean_size = file_size - rem
                self._mm.close()
                os.ftruncate(self._fd, clean_size)
                self._mm = mmap.mmap(self._fd, 0, access=mmap.ACCESS_WRITE)
                file_size = clean_size

            self._capacity_records = (file_size - JOURNAL_HEADER_SIZE) // SLOT_SIZE
            if self._record_count > self._capacity_records:
                self._record_count = self._capacity_records
                self._write_header()
        else:
            # Create fresh journal
            os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
            self._fd = os.open(
                self.filepath,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o644,
            )
            total_size = JOURNAL_HEADER_SIZE + (self.initial_records * SLOT_SIZE)
            os.ftruncate(self._fd, total_size)
            self._mm = mmap.mmap(self._fd, total_size, access=mmap.ACCESS_WRITE)
            self._capacity_records = self.initial_records
            self._record_count = 0
            self._first_seq = 0
            self._last_seq = 0
            self._created_ts = time.time()
            self._write_header()

    def _write_header(self) -> None:
        if not self._mm:
            return
        JOURNAL_HDR_STRUCT.pack_into(
            self._mm,
            0,
            JOURNAL_MAGIC,
            JOURNAL_VERSION,
            SLOT_SIZE,
            self.epoch,
            self._record_count,
            self._first_seq,
            self._last_seq,
            self._created_ts,
            time.time(),
            b"\x00" * 72,
        )

    def _ensure_capacity(self, needed_records: int = 1) -> None:
        if self._record_count + needed_records <= self._capacity_records:
            return

        new_capacity = max(
            self._capacity_records * GROWTH_FACTOR,
            self._record_count + needed_records + self.initial_records,
        )
        new_total_bytes = JOURNAL_HEADER_SIZE + (new_capacity * SLOT_SIZE)

        # Unmap current mmap before resizing
        self._mm.close()
        os.ftruncate(self._fd, new_total_bytes)
        self._mm = mmap.mmap(self._fd, new_total_bytes, access=mmap.ACCESS_WRITE)
        self._capacity_records = new_capacity

    def append_raw_slot(self, slot_bytes: bytes | memoryview | bytearray) -> int:
        """
        Append a pre-packed 128-byte SHM slot directly into the journal.
        Returns the committed sequence number.
        """
        if self._closed or not self._mm:
            raise RuntimeError("Journal is closed")
        if len(slot_bytes) != SLOT_SIZE:
            raise ValueError(f"Slot size must be {SLOT_SIZE} bytes, got {len(slot_bytes)}")

        self._ensure_capacity(1)

        offset = JOURNAL_HEADER_SIZE + (self._record_count * SLOT_SIZE)
        self._mm[offset : offset + SLOT_SIZE] = slot_bytes

        seq = struct.unpack_from("<Q", self._mm, offset)[0]
        if self._record_count == 0:
            self._first_seq = seq
        self._last_seq = seq
        self._record_count += 1

        # Periodically commit header
        if (self._record_count & 0x7F) == 0:
            self._write_header()

        return seq

    def append_tick(
        self,
        seq: int,
        symbol: str,
        source: str,
        price: float | None,
        size: float | None,
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
        event_type: int = EVENT_TYPE_TICK,
    ) -> int:
        """Pack and append a structured tick or depth event into the binary journal."""
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

        st_code = STATUS_MAP_REV.get(status, 1)

        sym_bytes = symbol.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
        src_bytes = source.encode("ascii", errors="replace")[:8].ljust(8, b"\x00")

        slot_buf = bytearray(SLOT_SIZE)
        SLOT_STRUCT.pack_into(
            slot_buf,
            0,
            seq,
            event_type,
            st_code,
            1 if is_crossed else 0,
            present,
            0,  # trunc
            b"\x00\x00\x00",
            exchange_ts,
            ingest_ts,
            broadcast_ts,
            engine_us,
            0,
            price if price is not None else 0.0,
            size if size is not None else 0.0,
            bid if bid is not None else 0.0,
            ask if ask is not None else 0.0,
            bid_size if bid_size is not None else 0.0,
            ask_size if ask_size is not None else 0.0,
            sym_bytes,
            src_bytes,
            b"\x00" * 8,
        )
        return self.append_raw_slot(slot_buf)

    def flush(self) -> None:
        """Synchronize in-memory journal pages to non-volatile storage."""
        if self._mm and not self._closed:
            self._write_header()
            self._mm.flush()

    def close(self, truncate_to_used: bool = False) -> None:
        """Close journal mapping and file descriptor."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._mm:
                self._write_header()
                self._mm.flush()
                self._mm.close()
                self._mm = None
            if self._fd is not None:
                if truncate_to_used:
                    used_bytes = JOURNAL_HEADER_SIZE + (self._record_count * SLOT_SIZE)
                    os.ftruncate(self._fd, used_bytes)
                os.close(self._fd)
                self._fd = None
        except Exception:
            pass

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def first_seq(self) -> int:
        return self._first_seq

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def __enter__(self) -> BinaryJournal:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class BinaryJournalReader:
    """
    Zero-copy memory-mapped reader for MDRAP binary journals.
    Permits sub-microsecond point lookups and sequential event streaming.
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Journal not found: {filepath}")

        self._fd = os.open(filepath, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        file_size = os.path.getsize(filepath)
        if file_size < JOURNAL_HEADER_SIZE:
            os.close(self._fd)
            raise ValueError("Corrupt journal: file smaller than header")

        self._mm = mmap.mmap(self._fd, 0, access=mmap.ACCESS_READ)
        (
            magic,
            ver,
            rec_sz,
            self.epoch,
            self.record_count,
            self.first_seq,
            self.last_seq,
            self.created_ts,
            self.updated_ts,
            _,
        ) = JOURNAL_HDR_STRUCT.unpack_from(self._mm, 0)

        if magic != JOURNAL_MAGIC:
            self.close()
            raise ValueError(f"Invalid journal magic: {magic!r}")
        if ver != JOURNAL_VERSION:
            self.close()
            raise ValueError(f"Unsupported journal version: {ver}")

        available_records = max(0, (file_size - JOURNAL_HEADER_SIZE) // SLOT_SIZE)
        if self.record_count > available_records:
            self.record_count = available_records

    def read_record(self, index: int) -> dict:
        """Unpack a specific 0-indexed record from the journal."""
        if index < 0 or index >= self.record_count:
            raise IndexError(f"Record index out of range: {index} (total={self.record_count})")

        offset = JOURNAL_HEADER_SIZE + (index * SLOT_SIZE)
        raw = SLOT_STRUCT.unpack_from(self._mm, offset)

        (
            seq,
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
        ) = raw

        sym = sym_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        src = src_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        status_str = STATUS_MAP_FWD.get(st_code, "UNKNOWN")

        price = p if (present & SHM3_PRESENT_PRICE) else None
        size = sz if (present & SHM3_PRESENT_SIZE) else None
        bid = bid_val if (present & SHM3_PRESENT_BID) else None
        ask = ask_val if (present & SHM3_PRESENT_ASK) else None
        bid_size = bid_sz if (present & SHM3_PRESENT_BSZ) else None
        ask_size = ask_sz if (present & SHM3_PRESENT_ASZ) else None

        if ev_type_code == EVENT_TYPE_DEPTH:
            return {
                "type": "DEPTH",
                "seq": seq,
                "sym": sym,
                "micro_price": price,
                "ofi": size,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
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
                "seq": seq,
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

    def __len__(self) -> int:
        return self.record_count

    def __iter__(self) -> Generator[dict, None, None]:
        for i in range(self.record_count):
            yield self.read_record(i)

    def scan_from_seq(self, start_seq: int) -> Generator[dict, None, None]:
        """Stream records sequentially starting from a sequence number."""
        # Check if sequences are contiguous
        if self.record_count == 0 or start_seq > self.last_seq:
            return

        # Direct index estimation for contiguous sequences
        estimated_idx = max(0, start_seq - self.first_seq)
        if estimated_idx < self.record_count:
            rec = self.read_record(estimated_idx)
            if rec["seq"] == start_seq:
                for i in range(estimated_idx, self.record_count):
                    yield self.read_record(i)
                return

        # Fallback linear scan if non-contiguous
        for i in range(self.record_count):
            rec = self.read_record(i)
            if rec["seq"] >= start_seq:
                yield rec

    def close(self) -> None:
        if self._mm:
            try:
                self._mm.close()
            except Exception:
                pass
            self._mm = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def __enter__(self) -> BinaryJournalReader:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
