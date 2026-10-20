"""Resilience tests for MDRAP binary sparse WAL index (Finding N-14).

Verifies that corrupted, shifted, truncated, stale, or deleted .idx files
never cause record dropping or false lookup misses in iter_from or seek_record.
"""

from __future__ import annotations

import glob
import os
import random
import struct
import tempfile
import pytest

from mdrap.ingestlog import IngestLog, build_sparse_index, load_sparse_index
from mdrap.models import RawEvent


def _build_log(n: int, interval: int = 16, seg_bytes: int = 4000) -> tuple[str, dict]:
    d = tempfile.mkdtemp()
    kw = dict(
        max_segment_bytes=seg_bytes,
        sparse_index_interval=interval,
        fsync_policy="never",
    )
    log = IngestLog(d, **kw)
    for i in range(n):
        log.append(
            RawEvent(
                source="SRC_TEST",
                payload={"i": i, "pad": "x" * 20},
                receive_timestamp=1000.0 + i,
                raw_id=f"r_{i}",
            )
        )
    log.close()
    return d, kw


def _scan_offsets(d: str, kw: dict, start: int = 0) -> list[int]:
    log = IngestLog(d, **kw)
    try:
        out = [
            item[0] if isinstance(item, tuple) else item
            for item in log.iter_from(start)
        ]
    finally:
        log.close()
    return out


def _seek_all(d: str, kw: dict, offsets: list[int]) -> list[tuple[int, int | None]]:
    log = IngestLog(d, **kw)
    bad = []
    try:
        for k in offsets:
            r = log.seek_record(k)
            got = (
                None
                if r is None
                else (r[0] if isinstance(r, tuple) else getattr(r, "offset", None))
            )
            if got != k:
                bad.append((k, got))
    finally:
        log.close()
    return bad


def test_sparse_index_basic_and_boundaries():
    """Verify normal index operation and boundary seek/iter_from."""
    n = 350
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        # Full scan
        assert _scan_offsets(d, kw, start=0) == list(range(n))

        # Boundary scans
        for s in (0, 1, 15, 16, 17, 100, 255, 256, 349):
            assert _scan_offsets(d, kw, start=s) == list(range(s, n))

        # Seek all offsets
        bad = _seek_all(d, kw, list(range(n)))
        assert bad == [], f"Mismatches in seek: {bad[:5]}"

        # Seek beyond bounds
        log = IngestLog(d, **kw)
        try:
            assert log.seek_record(-1) is None
            assert log.seek_record(n + 10) is None
        finally:
            log.close()
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_deleted_recovers_gracefully():
    """Verify that deleting all .idx files does not cause loss and auto-rebuilds."""
    n = 300
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        for idx_file in glob.glob(os.path.join(d, "*.idx")):
            os.remove(idx_file)

        # Seek still works and rebuilds indices
        bad = _seek_all(d, kw, list(range(0, n, 5)))
        assert bad == []

        # iter_from still works
        assert _scan_offsets(d, kw, start=120) == list(range(120, n))

        # Indices regenerated
        assert len(glob.glob(os.path.join(d, "*.idx"))) > 0
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_bitflip_corruption_self_healing():
    """Verify that bit flips in index files trigger detection and rebuild without data loss."""
    n = 300
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        rnd = random.Random(42)
        idx_files = sorted(glob.glob(os.path.join(d, "*.idx")))
        assert len(idx_files) > 0

        # Corrupt data in all index files
        for f in idx_files:
            b = bytearray(open(f, "rb").read())
            if len(b) > 36:
                for _ in range(max(1, len(b) // 40)):
                    target_byte = rnd.randrange(16, len(b))
                    b[target_byte] ^= 0xFF
            open(f, "wb").write(bytes(b))

        # seek_record and iter_from must detect corruption and self-heal
        bad = _seek_all(d, kw, list(range(n)))
        assert bad == [], f"Failed lookups after index corruption: {bad[:5]}"
        assert _scan_offsets(d, kw, start=100) == list(range(100, n))
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_garbage_and_truncated_headers():
    """Verify that zero-length, truncated, or garbage index files are rejected cleanly."""
    n = 300
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        idx_files = sorted(glob.glob(os.path.join(d, "*.idx")))
        if len(idx_files) >= 3:
            open(idx_files[0], "wb").write(b"")  # 0 bytes
            open(idx_files[1], "wb").write(b"GARBAGE_INDEX_BYTES" * 3)  # garbage
            open(idx_files[2], "r+b").truncate(10)  # truncated header

        bad = _seek_all(d, kw, list(range(n)))
        assert bad == []
        assert _scan_offsets(d, kw, start=50) == list(range(50, n))
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_shifted_file_positions():
    """Verify that structurally valid index entries with shifted file positions are caught at point of seek."""
    n = 300
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        idx_files = sorted(glob.glob(os.path.join(d, "*.idx")))
        if len(idx_files) >= 2:
            target_idx = idx_files[1]
            b = bytearray(open(target_idx, "rb").read())
            # Shift every file_pos by +3 bytes (structurally valid, semantically wrong)
            # Format: 36 byte header, then 16-byte entries (offset: 8, pos: 8)
            for i in range(36, len(b), 16):
                if i + 16 <= len(b):
                    off, pos = struct.unpack(">QQ", b[i : i + 16])
                    b[i : i + 16] = struct.pack(">QQ", off, pos + 3)
            open(target_idx, "wb").write(bytes(b))

        # Both seek_record and iter_from must verify the frame header at seek position
        # and fall back to linear scan instead of returning None or skipping records
        bad = _seek_all(d, kw, list(range(n)))
        assert bad == [], f"Shifted position seek failures: {bad[:5]}"

        iter_bad = [
            s for s in range(0, n, 10) if _scan_offsets(d, kw, s) != list(range(s, n))
        ]
        assert iter_bad == [], f"Shifted position iter_from failures: {iter_bad}"
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_torn_tail_recovery_and_append():
    """Verify that torn tail repair truncates the segment and rebuilds/invalidates the stale index."""
    n = 300
    d, kw = _build_log(n, interval=16, seg_bytes=3000)
    try:
        segs = sorted(glob.glob(os.path.join(d, "segment_*.log")))
        last_seg = segs[-1]
        sz = os.path.getsize(last_seg)
        with open(last_seg, "r+b") as f:
            f.truncate(sz - 37)  # Tear the final frame; stale .idx remains on disk

        # Reopen log: _recover_and_open repairs the segment and updates/invalidates index
        log = IngestLog(d, **kw)
        try:
            got = [o[0] if isinstance(o, tuple) else o for o in log.iter_from(0)]
            contiguous = got == list(range(len(got)))
            assert contiguous, f"Log not contiguous after recovery: len={len(got)}"

            # Seek across all recovered offsets using the open log
            bad = []
            for k in range(len(got)):
                r = log.seek_record(k)
                res_off = (
                    None
                    if r is None
                    else (r[0] if isinstance(r, tuple) else getattr(r, "offset", None))
                )
                if res_off != k:
                    bad.append((k, res_off))
            assert bad == [], f"Seek failures after torn tail: {bad[:5]}"

            # Append new records to the recovered segment
            new_off = log.append(
                RawEvent(
                    source="SRC_TEST",
                    payload={"after": 1},
                    receive_timestamp=9999.0,
                    raw_id="r_after",
                )
            )
            assert new_off == len(got)
        finally:
            log.close()

        final_scan = _scan_offsets(d, kw, 0)
        assert final_scan == list(range(len(got) + 1))
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def test_sparse_index_salvage_rebuilds_index():
    """Verify that IngestLog.salvage() rewrites corrupted rotated segments and updates the sparse index."""
    n = 350
    d, kw = _build_log(n, interval=16, seg_bytes=2500)
    try:
        segs = sorted(glob.glob(os.path.join(d, "segment_*.log")))
        assert len(segs) > 3
        corrupt_seg = segs[2]
        # Bit flip in middle of a rotated segment
        b = bytearray(open(corrupt_seg, "rb").read())
        b[len(b) // 2] ^= 0xFF
        open(corrupt_seg, "wb").write(bytes(b))

        # Run salvage
        rep = IngestLog.salvage(d, backup=True)
        assert rep["corrupted_segments"] >= 1
        assert corrupt_seg in rep["repaired_files"]

        # Post-salvage: iter_from and seek_record must match 100%
        got = _scan_offsets(d, kw, 0)
        assert len(got) > 0
        assert all(a < b for a, b in zip(got, got[1:])), (
            "Offsets not strictly increasing"
        )

        # Verify seek_record matches iter_from on every single salvaged record
        log = IngestLog(d, **kw)
        try:
            mismatches = []
            for k in got:
                r = log.seek_record(k)
                res_off = (
                    None
                    if r is None
                    else (r[0] if isinstance(r, tuple) else getattr(r, "offset", None))
                )
                if res_off != k:
                    mismatches.append((k, res_off))
            assert mismatches == [], (
                f"seek_record vs iter_from mismatches after salvage: {mismatches[:5]}"
            )
        finally:
            log.close()
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)
