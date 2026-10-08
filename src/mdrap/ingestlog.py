"""Durable Segmented Write-Ahead IngestLog for MDRAP.

Provides write-ahead event durability with segmented files, length framing,
CRC32 validation, torn-tail detection, segment rotation, configurable fsync
policies, and checkpoint-aware retention.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import threading
import time
import zlib
from typing import Iterator

from .models import RawEvent

__stability__ = "stable"


logger = logging.getLogger(__name__)

# Constants & Binary Layout
LOG_MAGIC = b"MDRAPILG"  # 8 bytes
LOG_VERSION = 1          # uint16 (2 bytes)
SEGMENT_HEADER_FORMAT = ">8sHQd6s"  # magic(8), ver(2), start_offset(8), ts(8), res(6) = 32 bytes
SEGMENT_HEADER_SIZE = struct.calcsize(SEGMENT_HEADER_FORMAT)  # 32

FRAME_MAGIC = 0xAA55     # uint16 (2 bytes)
FRAME_HEADER_FORMAT = ">HHQdII"  # magic(2), res(2), offset(8), ts(8), length(4), crc32(4) = 28 bytes
FRAME_HEADER_SIZE = struct.calcsize(FRAME_HEADER_FORMAT)  # 28

DEFAULT_MAX_SEGMENT_BYTES = 10 * 1024 * 1024  # 10 MB


class IngestLogError(Exception):
    """Base exception for IngestLog errors."""


class IngestLogCorruptError(IngestLogError):
    """Raised when an unrecoverable bit-flip or corruption is detected in the log body."""


class IngestLogLockedError(IngestLogError):
    """Raised when IngestLog directory is already locked by another process or instance."""


class IngestLog:
    """Segmented write-ahead log providing strictly framed event persistence."""

    def __init__(
        self,
        log_dir: str,
        fsync_policy: str = "always",
        max_segment_bytes: int = DEFAULT_MAX_SEGMENT_BYTES,
        lock: bool = True,
    ):
        self.log_dir = os.path.abspath(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        self.fsync_policy = fsync_policy.lower()
        if self.fsync_policy not in ("always", "grouped_by_time", "grouped_by_size", "never"):
            raise ValueError(f"Invalid fsync_policy: {self.fsync_policy}")

        self.max_segment_bytes = max_segment_bytes
        self._lock = threading.RLock()
        self._next_offset = 0
        self._current_file = None
        self._current_filename: str | None = None
        self._current_segment_start_offset = 0
        self._bytes_since_fsync = 0
        self._last_fsync_ts = time.time()
        self._is_poisoned: bool = False
        self._lock_file = None

        if lock:
            self._acquire_process_lock()

        self._recover_and_open()

    def _acquire_process_lock(self) -> None:
        """Acquire an exclusive advisory lock on the log directory to prevent concurrent writers."""
        lock_path = os.path.join(self.log_dir, ".lock")
        try:
            self._lock_file = open(lock_path, "a+b")
            self._lock_file.seek(0, os.SEEK_END)
            if self._lock_file.tell() == 0:
                self._lock_file.write(b"MDRAP_LOCK\n")
                self._lock_file.flush()
            self._lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            if self._lock_file is not None:
                try:
                    self._lock_file.close()
                except Exception:
                    pass
                self._lock_file = None
            raise IngestLogLockedError(
                f"IngestLog directory '{self.log_dir}' is locked by another process or instance"
            ) from exc

    def _release_process_lock(self) -> None:
        """Release the exclusive advisory lock on the log directory."""
        if self._lock_file is not None and not self._lock_file.closed:
            try:
                self._lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None

    @property
    def next_offset(self) -> int:
        with self._lock:
            return self._next_offset

    def _segment_filename(self, start_offset: int) -> str:
        return os.path.join(self.log_dir, f"segment_{start_offset:012d}.log")

    def _list_segment_files(self) -> list[tuple[int, str]]:
        """List sorted (start_offset, filepath) for all segment files in log_dir."""
        segments = []
        if not os.path.exists(self.log_dir):
            return []
        for fname in os.listdir(self.log_dir):
            if fname.startswith("segment_") and fname.endswith(".log"):
                parts = fname[len("segment_") : -len(".log")]
                try:
                    start_offset = int(parts)
                    segments.append((start_offset, os.path.join(self.log_dir, fname)))
                except ValueError:
                    continue
        segments.sort(key=lambda x: x[0])
        return segments

    def _recover_and_open(self) -> None:
        """Scan existing segments, recover torn tails, and open the active segment."""
        with self._lock:
            segments = self._list_segment_files()
            if not segments:
                self._rotate_to_new_segment(0)
                return

            valid_segments = []
            highest_offset = -1
            for start_off, path in segments:
                highest_in_seg = self._verify_and_repair_segment(path)
                if os.path.exists(path) and os.path.getsize(path) >= SEGMENT_HEADER_SIZE:
                    valid_segments.append((start_off, path))
                if highest_in_seg > highest_offset:
                    highest_offset = highest_in_seg

            self._next_offset = highest_offset + 1 if highest_offset >= 0 else 0

            if not valid_segments:
                self._rotate_to_new_segment(self._next_offset)
                return

            last_start_offset, last_filepath = valid_segments[-1]
            # Open last segment in append/update mode
            self._current_segment_start_offset = last_start_offset
            self._current_filename = last_filepath
            self._current_file = open(last_filepath, "a+b")
            self._current_file.seek(0, os.SEEK_END)

    def _verify_and_repair_segment(self, filepath: str) -> int:
        """Verify framing & CRC of a segment. Repair/truncate torn tail if present.

        Returns highest valid offset found in this segment (-1 if empty).
        """
        if not os.path.isfile(filepath):
            return -1

        size = os.path.getsize(filepath)
        if size < SEGMENT_HEADER_SIZE:
            # File smaller than header -> torn header, remove file so it is not reopened headerless
            logger.warning("Segment file %s smaller than header (%d < %d); removing torn segment", filepath, size, SEGMENT_HEADER_SIZE)
            try:
                os.remove(filepath)
            except OSError:
                with open(filepath, "wb") as f:
                    f.truncate(0)
            return -1

        highest_offset = -1
        valid_pos = 0

        with open(filepath, "r+b") as f:
            header_bytes = f.read(SEGMENT_HEADER_SIZE)
            magic, ver, seg_start, ts, _ = struct.unpack(SEGMENT_HEADER_FORMAT, header_bytes)
            if magic != LOG_MAGIC or ver != LOG_VERSION:
                raise IngestLogCorruptError(f"Corrupt segment header in {filepath}: magic={magic!r}, ver={ver}")

            valid_pos = SEGMENT_HEADER_SIZE

            while True:
                pos = f.tell()
                frame_hdr_bytes = f.read(FRAME_HEADER_SIZE)
                if not frame_hdr_bytes:
                    # Clean EOF
                    break
                if len(frame_hdr_bytes) < FRAME_HEADER_SIZE:
                    # Torn tail during frame header write at EOF!
                    logger.warning("Torn tail detected in %s at pos %d (< frame header); truncating", filepath, pos)
                    f.seek(valid_pos)
                    f.truncate(valid_pos)
                    f.flush()
                    break

                f_magic, _, offset, f_ts, length, expected_crc = struct.unpack(FRAME_HEADER_FORMAT, frame_hdr_bytes)
                if f_magic != FRAME_MAGIC:
                    # Check if this is zero-padded tail at EOF or mid-segment corruption
                    f.seek(0, os.SEEK_END)
                    is_at_end = (f.tell() <= pos + FRAME_HEADER_SIZE)
                    if is_at_end and frame_hdr_bytes == b"\x00" * FRAME_HEADER_SIZE:
                        logger.warning("Zeroed tail frame detected at pos %d in %s; truncating torn tail", pos, filepath)
                        f.seek(valid_pos)
                        f.truncate(valid_pos)
                        f.flush()
                        break
                    else:
                        raise IngestLogCorruptError(
                            f"Invalid frame magic 0x{f_magic:04x} in {filepath} at pos {pos} (offset={offset}): corrupted segment"
                        )

                payload_bytes = f.read(length)
                if len(payload_bytes) < length:
                    # Incomplete final frame at EOF!
                    logger.warning("Incomplete payload (%d < %d) at pos %d in %s; truncating torn frame", len(payload_bytes), length, pos, filepath)
                    f.seek(valid_pos)
                    f.truncate(valid_pos)
                    f.flush()
                    break

                actual_crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    # CRC corruption!
                    # If this is the last frame before EOF, treat as torn write; if middle of file, corrupt error!
                    f.seek(0, os.SEEK_END)
                    is_at_end = (f.tell() == pos + FRAME_HEADER_SIZE + length)
                    if is_at_end:
                        logger.warning("CRC mismatch on final frame in %s; removing torn frame", filepath)
                        f.seek(valid_pos)
                        f.truncate(valid_pos)
                        f.flush()
                        break
                    else:
                        raise IngestLogCorruptError(
                            f"CRC mismatch in middle of {filepath} at offset {offset}: expected {expected_crc:#010x}, got {actual_crc:#010x}"
                        )

                valid_pos = pos + FRAME_HEADER_SIZE + length
                if offset > highest_offset:
                    highest_offset = offset

        return highest_offset

    def _rotate_to_new_segment(self, start_offset: int) -> None:
        """Create and open a new segment file with a versioned header."""
        if self._current_file:
            try:
                self._current_file.flush()
                os.fsync(self._current_file.fileno())
                self._current_file.close()
            except Exception as exc:
                logger.debug("Closing previous segment raised: %s", exc)

        filename = self._segment_filename(start_offset)
        hdr = struct.pack(
            SEGMENT_HEADER_FORMAT,
            LOG_MAGIC,
            LOG_VERSION,
            start_offset,
            time.time(),
            b"\x00" * 6,
        )
        with open(filename, "wb") as f:
            f.write(hdr)
            f.flush()
            os.fsync(f.fileno())

        # Durable directory sync on POSIX
        if os.name != "nt":
            try:
                dir_fd = os.open(self.log_dir, os.O_RDONLY)
                os.fsync(dir_fd)
                os.close(dir_fd)
            except Exception:
                pass

        self._current_filename = filename
        self._current_segment_start_offset = start_offset
        self._current_file = open(filename, "a+b")
        self._current_file.seek(0, os.SEEK_END)
        self._bytes_since_fsync = 0

    def append(self, raw: RawEvent | dict) -> int:
        """Append an event to the log. Returns the assigned offset after durability ACK.

        Never ACK before the configured durability boundary is satisfied!
        """
        with self._lock:
            if self._is_poisoned:
                raise IngestLogError(
                    "IngestLog is in a poisoned state due to unrecoverable I/O rollback failure; reopen required"
                )

            if isinstance(raw, RawEvent):
                recv_ts = raw.receive_timestamp if raw.receive_timestamp else time.time()
                record = {
                    "raw_id": raw.raw_id,
                    "source": raw.source,
                    "payload": raw.payload,
                    "receive_timestamp": recv_ts,
                }
            elif isinstance(raw, dict):
                record = dict(raw)
                if not record.get("receive_timestamp"):
                    record["receive_timestamp"] = time.time()
            else:
                raise TypeError(f"Expected RawEvent or dict, got {type(raw).__name__}")

            payload_bytes = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8", errors="replace")
            payload_len = len(payload_bytes)
            crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF

            offset = self._next_offset
            now = time.time()
            frame_hdr = struct.pack(
                FRAME_HEADER_FORMAT,
                FRAME_MAGIC,
                0,
                offset,
                now,
                payload_len,
                crc,
            )

            # Check if active segment exceeds max_segment_bytes
            if self._current_file is None:
                self._rotate_to_new_segment(offset)
            else:
                curr_size = self._current_file.tell()
                if curr_size + FRAME_HEADER_SIZE + payload_len > self.max_segment_bytes:
                    self._rotate_to_new_segment(offset)

            # Record write position for rollback on IO failure (e.g. disk full)
            write_pos = self._current_file.tell()
            try:
                # Write frame
                self._current_file.write(frame_hdr)
                self._current_file.write(payload_bytes)
                # Flush userspace stdio buffers to OS kernel on every append before ACK!
                self._current_file.flush()
                frame_total_len = FRAME_HEADER_SIZE + payload_len
                self._bytes_since_fsync += frame_total_len

                # Durability policy execution before ACK
                if self.fsync_policy == "always":
                    os.fsync(self._current_file.fileno())
                    self._bytes_since_fsync = 0
                elif self.fsync_policy == "grouped_by_size":
                    if self._bytes_since_fsync >= 64 * 1024:  # 64 KB threshold
                        os.fsync(self._current_file.fileno())
                        self._bytes_since_fsync = 0
                elif self.fsync_policy == "grouped_by_time":
                    now_ts = time.time()
                    if now_ts - self._last_fsync_ts >= 0.05:  # 50 ms
                        os.fsync(self._current_file.fileno())
                        self._last_fsync_ts = now_ts
                        self._bytes_since_fsync = 0

                # Advance offset ONLY after successful write, flush, and fsync
                self._next_offset += 1
            except Exception:
                try:
                    self._current_file.seek(write_pos)
                    self._current_file.truncate(write_pos)
                    self._current_file.flush()
                except Exception:
                    self._is_poisoned = True
                raise

            return offset

    def flush(self) -> None:
        """Force flush and fsync of the current active segment."""
        with self._lock:
            if self._current_file and not self._current_file.closed:
                self._current_file.flush()
                os.fsync(self._current_file.fileno())
                self._bytes_since_fsync = 0
                self._last_fsync_ts = time.time()

    def close(self) -> None:
        """Close current active segment and release process lock."""
        with self._lock:
            if self._current_file and not self._current_file.closed:
                try:
                    self._current_file.flush()
                    os.fsync(self._current_file.fileno())
                    self._current_file.close()
                except Exception:
                    pass
                self._current_file = None
            self._release_process_lock()

    def __enter__(self) -> IngestLog:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def iter_from(self, start_offset: int = 0) -> Iterator[tuple[int, RawEvent]]:
        """Iterate all events from start_offset across all segment files in order."""
        with self._lock:
            self.flush()
            segments = self._list_segment_files()

        for seg_start, path in segments:
            if not os.path.isfile(path) or os.path.getsize(path) < SEGMENT_HEADER_SIZE:
                continue

            with open(path, "rb") as f:
                hdr_bytes = f.read(SEGMENT_HEADER_SIZE)
                if len(hdr_bytes) < SEGMENT_HEADER_SIZE:
                    continue
                magic, ver, _, _, _ = struct.unpack(SEGMENT_HEADER_FORMAT, hdr_bytes)
                if magic != LOG_MAGIC or ver != LOG_VERSION:
                    continue

                while True:
                    frame_hdr_bytes = f.read(FRAME_HEADER_SIZE)
                    if len(frame_hdr_bytes) < FRAME_HEADER_SIZE:
                        break
                    f_magic, _, offset, ts, length, expected_crc = struct.unpack(FRAME_HEADER_FORMAT, frame_hdr_bytes)
                    if f_magic != FRAME_MAGIC:
                        break
                    payload_bytes = f.read(length)
                    if len(payload_bytes) < length:
                        break
                    if (zlib.crc32(payload_bytes) & 0xFFFFFFFF) != expected_crc:
                        continue

                    if offset >= start_offset:
                        try:
                            rec = json.loads(payload_bytes.decode("utf-8"))
                            raw_evt = RawEvent(
                                source=rec.get("source", ""),
                                payload=rec.get("payload", {}),
                                receive_timestamp=rec.get("receive_timestamp") or ts,
                                raw_id=rec.get("raw_id"),
                            )
                            yield offset, raw_evt
                        except Exception as exc:
                            logger.warning("Failed to decode record at offset %d: %s", offset, exc)

    def delete_segments_before(self, checkpoint_offset: int) -> int:
        """Delete obsolete segment files where all records are strictly before checkpoint_offset.

        Never deletes the active (last) segment!
        Returns number of deleted segment files.
        """
        with self._lock:
            segments = self._list_segment_files()
            if len(segments) <= 1:
                return 0  # Cannot delete active segment

            deleted_count = 0
            # Inspect segments except the last one (active segment)
            for i in range(len(segments) - 1):
                seg_start, path = segments[i]
                next_start, _ = segments[i + 1]
                # If all records in this segment are strictly < checkpoint_offset
                if next_start <= checkpoint_offset:
                    try:
                        os.remove(path)
                        deleted_count += 1
                        logger.info("Purged obsolete segment %s (max offset < %d)", path, checkpoint_offset)
                    except OSError as exc:
                        logger.warning("Could not delete segment %s: %s", path, exc)

            return deleted_count

    @classmethod
    def salvage(cls, log_dir: str, backup: bool = True) -> dict[str, Any]:
        """Salvage corrupted segments by extracting all recoverable frames.

        Scans segment files in log_dir. If corruption or bit-flips are encountered,
        all valid frames (matching FRAME_MAGIC and valid CRC32) are extracted and
        written into clean segment files.
        Original files are preserved with .bak extensions if backup is True.

        Returns a summary dict with recovery telemetry.
        """
        abs_log_dir = os.path.abspath(log_dir)
        if not os.path.exists(abs_log_dir):
            return {
                "scanned_segments": 0,
                "corrupted_segments": 0,
                "recovered_records": 0,
                "repaired_files": [],
            }

        report: dict[str, Any] = {
            "scanned_segments": 0,
            "corrupted_segments": 0,
            "recovered_records": 0,
            "repaired_files": [],
        }

        seg_files = []
        for fname in os.listdir(abs_log_dir):
            if fname.startswith("segment_") and fname.endswith(".log"):
                seg_files.append(os.path.join(abs_log_dir, fname))
        seg_files.sort()

        for s_path in seg_files:
            report["scanned_segments"] += 1
            s_size = os.path.getsize(s_path)
            is_corrupt = False
            recovered_in_file: list[tuple[bytes, bytes]] = []

            if s_size < SEGMENT_HEADER_SIZE:
                is_corrupt = True
            else:
                with open(s_path, "rb") as f:
                    hdr_bytes = f.read(SEGMENT_HEADER_SIZE)
                    if len(hdr_bytes) < SEGMENT_HEADER_SIZE:
                        is_corrupt = True
                    else:
                        magic, ver, seg_start, ts, _ = struct.unpack(SEGMENT_HEADER_FORMAT, hdr_bytes)
                        if magic != LOG_MAGIC or ver != LOG_VERSION:
                            is_corrupt = True

                        while True:
                            pos = f.tell()
                            frame_hdr_bytes = f.read(FRAME_HEADER_SIZE)
                            if not frame_hdr_bytes:
                                break
                            if len(frame_hdr_bytes) < FRAME_HEADER_SIZE:
                                is_corrupt = True
                                break

                            f_magic, _, offset, f_ts, length, expected_crc = struct.unpack(FRAME_HEADER_FORMAT, frame_hdr_bytes)
                            if f_magic != FRAME_MAGIC or length > DEFAULT_MAX_SEGMENT_BYTES:
                                is_corrupt = True
                                f.seek(pos + 1)
                                while True:
                                    search_byte = f.read(1)
                                    if not search_byte:
                                        break
                                    if search_byte == b"\xaa":
                                        next_byte = f.read(1)
                                        if next_byte == b"\x55":
                                            f.seek(f.tell() - 2)
                                            break
                                        else:
                                            f.seek(f.tell() - 1)
                                continue

                            payload_bytes = f.read(length)
                            if len(payload_bytes) < length:
                                is_corrupt = True
                                break

                            actual_crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
                            if actual_crc != expected_crc:
                                is_corrupt = True
                                continue

                            recovered_in_file.append((frame_hdr_bytes, payload_bytes))

            if is_corrupt:
                report["corrupted_segments"] += 1
                if backup:
                    import shutil
                    bak_path = s_path + f".bak.{int(time.time())}"
                    try:
                        shutil.copy2(s_path, bak_path)
                    except Exception as exc:
                        logger.warning("Could not create backup %s: %s", bak_path, exc)

                if recovered_in_file:
                    first_hdr = recovered_in_file[0][0]
                    _, _, start_off, first_ts, _, _ = struct.unpack(FRAME_HEADER_FORMAT, first_hdr)
                    clean_hdr = struct.pack(
                        SEGMENT_HEADER_FORMAT,
                        LOG_MAGIC,
                        LOG_VERSION,
                        start_off,
                        first_ts,
                        b"\x00" * 6,
                    )
                    with open(s_path, "wb") as f_out:
                        f_out.write(clean_hdr)
                        for f_hdr, p_bytes in recovered_in_file:
                            f_out.write(f_hdr)
                            f_out.write(p_bytes)
                        f_out.flush()
                        os.fsync(f_out.fileno())
                    report["repaired_files"].append(s_path)
                    report["recovered_records"] += len(recovered_in_file)
                else:
                    try:
                        os.remove(s_path)
                    except OSError:
                        pass
            else:
                report["recovered_records"] += len(recovered_in_file)

        return report

