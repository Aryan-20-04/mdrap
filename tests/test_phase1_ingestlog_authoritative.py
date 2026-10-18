"""Acceptance test suite for Phase 1: Authoritative IngestLog Durability & Deterministic Replay.

Tests:
1. Binary framing, magic headers, and CRC-32 checksums.
2. Segment rotation and cryptographic manifest hash chains (SHA-256).
3. Tamper detection: mutating a single byte in a sealed segment triggers manifest verification failure.
4. Broken hash chain and root hash corruption detection.
5. Crash recovery matrix: torn frame headers, incomplete payloads, corrupted EOF CRCs, and zero-padded tails.
6. Deterministic replay equivalence across multiple independent Engine folds.
7. CLI integration for mdrap replay --from-wal and --verify-equivalence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import time
import zlib
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine, EngineState
from mdrap.ingestlog import (
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    GENESIS_HASH,
    LOG_MAGIC,
    LOG_VERSION,
    MANIFEST_FILENAME,
    SEGMENT_HEADER_FORMAT,
    SEGMENT_HEADER_SIZE,
    IngestLog,
    IngestLogCorruptError,
    LogManifest,
    SegmentMetadata,
    compute_frame_crc,
)
from mdrap.models import EventType, QualityStatus, RawEvent
from mdrap.replay import HistoricalReplayEngine
from mdrap.cli.operations import cmd_replay


class TestIngestLogFramingAndManifest:
    """Verifies binary framing layout and cryptographic manifest integrity."""

    def test_binary_framing_and_checksum_integrity(self, tmp_path):
        log_dir = str(tmp_path / "wal_framing")
        log = IngestLog(log_dir=log_dir, fsync_policy="always")

        raw = RawEvent(
            source="CME",
            payload={
                "instrument": "NQ",
                "price": 18500.25,
                "quantity": 10.0,
                "exchange_ts": 1729000000.0,
                "sequence": 101,
            },
            receive_timestamp=1729000000.001,
            raw_id="raw_cme_101",
        )
        offset = log.append(raw)
        assert offset == 0
        log.close()

        # Read binary layout directly from disk
        segments = log._list_segment_files()
        assert len(segments) == 1
        seg_path = segments[0][1]

        with open(seg_path, "rb") as f:
            # Segment Header (32 bytes)
            hdr_bytes = f.read(SEGMENT_HEADER_SIZE)
            assert len(hdr_bytes) == SEGMENT_HEADER_SIZE
            magic, ver, start_off, ts, res = struct.unpack(
                SEGMENT_HEADER_FORMAT, hdr_bytes
            )
            assert magic == LOG_MAGIC
            assert ver == LOG_VERSION
            assert start_off == 0

            # Frame Header (28 bytes)
            frame_hdr_bytes = f.read(FRAME_HEADER_SIZE)
            assert len(frame_hdr_bytes) == FRAME_HEADER_SIZE
            f_magic, _, f_off, f_ts, length, crc32 = struct.unpack(
                FRAME_HEADER_FORMAT, frame_hdr_bytes
            )
            assert f_magic == FRAME_MAGIC
            assert f_off == 0
            assert length > 0

            # Frame Payload
            payload_bytes = f.read(length)
            assert len(payload_bytes) == length
            assert (
                compute_frame_crc(frame_hdr_bytes[:24], payload_bytes, version=ver)
                == crc32
            )

            payload_data = json.loads(payload_bytes.decode("utf-8"))
            assert payload_data["source"] == "CME"
            assert payload_data["raw_id"] == "raw_cme_101"

    def test_segment_rotation_manifest_and_hash_chain(self, tmp_path):
        log_dir = str(tmp_path / "wal_rotation")
        # Small max_segment_bytes to trigger rotation every few records
        log = IngestLog(log_dir=log_dir, max_segment_bytes=512, fsync_policy="always")

        for i in range(20):
            raw = RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "price": 150.0 + i,
                    "exchange_ts": 1729000000.0 + i,
                    "sequence": i,
                },
                receive_timestamp=1729000000.0 + i,
                raw_id=f"aapl_{i}",
            )
            log.append(raw)

        log.close()

        # Manifest must exist
        manifest_path = os.path.join(log_dir, MANIFEST_FILENAME)
        assert os.path.exists(manifest_path)

        with open(manifest_path, "r", encoding="utf-8") as f:
            m_data = json.load(f)
        manifest = LogManifest.from_dict(m_data)

        # Must have rotated into multiple segments
        assert len(manifest.segments) >= 3
        assert manifest.schema_version == 1

        # Check hash chain continuity
        assert manifest.segments[0].prev_sha256 == GENESIS_HASH
        expected_cum = 0
        for idx, seg in enumerate(manifest.segments):
            expected_cum += seg.event_count
            assert seg.cumulative_event_count == expected_cum
            if idx > 0:
                assert seg.prev_sha256 == manifest.segments[idx - 1].sha256
                assert seg.start_offset == manifest.segments[idx - 1].end_offset + 1

        # Manifest verification API must pass cleanly
        m_report = IngestLog.verify_manifest(log_dir)
        assert m_report["is_valid"] is True
        assert m_report["errors"] == []
        assert m_report["total_segments"] == len(manifest.segments)
        assert m_report["total_events"] == 20

        # General verify API must also report manifest_verified: True
        v_report = IngestLog.verify(log_dir)
        assert v_report["is_clean"] is True
        assert v_report["manifest_verified"] is True

    def test_manifest_tampering_detection(self, tmp_path):
        log_dir = str(tmp_path / "wal_tamper")
        log = IngestLog(log_dir=log_dir, max_segment_bytes=512, fsync_policy="always")
        for i in range(15):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "TSLA", "price": 200.0 + i}
                )
            )
        log.close()

        # Verify initial clean state
        clean_rep = IngestLog.verify_manifest(log_dir)
        assert clean_rep["is_valid"] is True

        # Tamper: mutate 1 byte in the first segment file
        seg0_path = os.path.join(log_dir, clean_rep["segments"][0]["segment_filename"])
        with open(seg0_path, "r+b") as f:
            f.seek(SEGMENT_HEADER_SIZE + 5)
            orig_byte = f.read(1)
            f.seek(SEGMENT_HEADER_SIZE + 5)
            # Flip bits of this byte
            f.write(bytes([orig_byte[0] ^ 0xFF]))

        # Manifest verification must detect the tampering
        tamper_rep = IngestLog.verify_manifest(log_dir)
        assert tamper_rep["is_valid"] is False
        assert any("SHA-256 tampered" in err for err in tamper_rep["errors"])

        # General verify API must also fail
        v_rep = IngestLog.verify(log_dir)
        assert v_rep["is_clean"] is False
        assert v_rep["manifest_verified"] is False

    def test_manifest_broken_hash_chain_detection(self, tmp_path):
        log_dir = str(tmp_path / "wal_chain_break")
        log = IngestLog(log_dir=log_dir, max_segment_bytes=512, fsync_policy="always")
        for i in range(15):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "NVDA", "price": 120.0 + i}
                )
            )
        log.close()

        manifest_path = os.path.join(log_dir, MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Break hash chain on segment 1
        data["segments"][1]["prev_sha256"] = "f" * 64
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # Verification must fail
        rep = IngestLog.verify_manifest(log_dir)
        assert rep["is_valid"] is False
        assert any("hash chain broken" in err for err in rep["errors"])


class TestCrashRecoveryMatrix:
    """Verifies torn writes and fault-injections at EOF boundaries."""

    def test_crash_torn_header_at_eof(self, tmp_path):
        log_dir = str(tmp_path / "crash_torn_hdr")
        log = IngestLog(log_dir=log_dir, fsync_policy="always")
        for i in range(5):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "SPY", "price": 500.0 + i}
                )
            )
        log.close()

        # Corrupt: inject torn 12-byte fragment of a frame header at EOF
        seg_file = log._list_segment_files()[0][1]
        valid_size = os.path.getsize(seg_file)
        with open(seg_file, "ab") as f:
            f.write(b"SHORT_HDR_12")

        assert os.path.getsize(seg_file) == valid_size + 12

        # Reopen: should auto-truncate back to valid_size and recover all 5 events
        reopened = IngestLog(log_dir=log_dir)
        assert os.path.getsize(seg_file) == valid_size
        recovered = list(reopened.iter_from(0))
        assert len(recovered) == 5
        assert reopened.next_offset == 5

        # Monotonic resumption
        new_off = reopened.append(
            RawEvent(source="TEST", payload={"instrument": "SPY", "price": 505.0})
        )
        assert new_off == 5
        reopened.close()

    def test_crash_incomplete_payload_at_eof(self, tmp_path):
        log_dir = str(tmp_path / "crash_torn_payload")
        log = IngestLog(log_dir=log_dir, fsync_policy="always")
        for i in range(4):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "QQQ", "price": 450.0 + i}
                )
            )
        log.close()

        seg_file = log._list_segment_files()[0][1]
        valid_size = os.path.getsize(seg_file)

        # Inject frame header declaring 128 bytes, but write only 30 bytes of payload
        with open(seg_file, "ab") as f:
            hdr = struct.pack(
                FRAME_HEADER_FORMAT,
                FRAME_MAGIC,
                0,
                4,
                1729000000.0,
                128,  # declared 128 bytes
                999999,
            )
            f.write(hdr)
            f.write(b"X" * 30)

        # Reopen: detects incomplete payload, auto-truncates torn frame
        reopened = IngestLog(log_dir=log_dir)
        assert os.path.getsize(seg_file) == valid_size
        assert len(list(reopened.iter_from(0))) == 4
        assert reopened.next_offset == 4
        reopened.close()

    def test_crash_corrupted_crc_at_eof(self, tmp_path):
        log_dir = str(tmp_path / "crash_corrupted_crc")
        log = IngestLog(log_dir=log_dir, fsync_policy="always")
        for i in range(3):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "IWM", "price": 200.0 + i}
                )
            )
        log.close()

        seg_file = log._list_segment_files()[0][1]
        valid_size = os.path.getsize(seg_file)

        # Inject complete frame at EOF with an invalid CRC32 (simulating power failure during disk flush)
        payload = b'{"source":"TEST","payload":{"instrument":"IWM","price":203.0}}'
        bad_crc = 0xDEADBEEF
        with open(seg_file, "ab") as f:
            hdr = struct.pack(
                FRAME_HEADER_FORMAT,
                FRAME_MAGIC,
                0,
                3,
                1729000000.0,
                len(payload),
                bad_crc,
            )
            f.write(hdr)
            f.write(payload)

        # Reopen: torn final frame with bad CRC at EOF is removed
        reopened = IngestLog(log_dir=log_dir)
        assert os.path.getsize(seg_file) == valid_size
        assert len(list(reopened.iter_from(0))) == 3
        reopened.close()

    def test_crash_zeroed_preallocated_tail_at_eof(self, tmp_path):
        log_dir = str(tmp_path / "crash_zeroed_tail")
        log = IngestLog(log_dir=log_dir, fsync_policy="always")
        for i in range(3):
            log.append(
                RawEvent(
                    source="TEST", payload={"instrument": "DIA", "price": 380.0 + i}
                )
            )
        log.close()

        seg_file = log._list_segment_files()[0][1]
        valid_size = os.path.getsize(seg_file)

        # Inject 128 bytes of zero-padding at EOF
        with open(seg_file, "ab") as f:
            f.write(b"\x00" * 128)

        reopened = IngestLog(log_dir=log_dir)
        assert os.path.getsize(seg_file) == valid_size
        assert len(list(reopened.iter_from(0))) == 3
        reopened.close()


class TestDeterministicReplayEquivalence:
    """Verifies deterministic replay equivalence across multiple independent engine folds."""

    def test_verify_replay_equivalence_multi_fold(self, tmp_path):
        log_dir = str(tmp_path / "replay_wal")
        log = IngestLog(log_dir=log_dir, max_segment_bytes=1024, fsync_policy="always")

        # Ingest a diverse sequence of events: valid trades, quotes, duplicates, and out-of-order events
        events = [
            # Normal trade
            RawEvent(
                source="CME",
                payload={
                    "instrument": "NQ",
                    "event_type": "TRADE",
                    "price": 18500.0,
                    "quantity": 5.0,
                    "exchange_ts": 1729000000.1,
                    "sequence": 1,
                },
            ),
            # Normal quote
            RawEvent(
                source="CME",
                payload={
                    "instrument": "NQ",
                    "event_type": "QUOTE",
                    "bid": 18499.5,
                    "ask": 18500.5,
                    "exchange_ts": 1729000000.2,
                    "sequence": 2,
                },
            ),
            # Duplicate sequence event
            RawEvent(
                source="CME",
                payload={
                    "instrument": "NQ",
                    "event_type": "TRADE",
                    "price": 18500.0,
                    "quantity": 5.0,
                    "exchange_ts": 1729000000.1,
                    "sequence": 1,
                },
            ),
            # Another instrument trade
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 220.50,
                    "quantity": 100.0,
                    "exchange_ts": 1729000000.3,
                    "sequence": 10,
                },
            ),
            # Stale / crossed quote (should be flagged by engine)
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "QUOTE",
                    "bid": 221.00,
                    "ask": 220.00,
                    "exchange_ts": 1729000000.4,
                    "sequence": 11,
                },
            ),
        ]

        for ev in events:
            log.append(ev)
        log.close()

        # Run 3 independent engine replay folds
        res = HistoricalReplayEngine.verify_replay_equivalence(
            log_dir, iterations=3, clock_ts=1729000000.0
        )

        assert res["is_equivalent"] is True
        assert res["events_replayed"] == len(events)
        assert res["iterations"] == 3
        assert res["errors"] == []

        # All 3 runs must have identical state hashes and event stream hashes
        assert len(res["runs"]) == 3
        h0_state = res["runs"][0]["state_hash"]
        h0_events = res["runs"][0]["events_hash"]
        assert all(r["state_hash"] == h0_state for r in res["runs"])
        assert all(r["events_hash"] == h0_events for r in res["runs"])

    def test_cli_replay_verify_equivalence(self, tmp_path, capsys):
        wal_dir = str(tmp_path / "cli_wal")
        log = IngestLog(log_dir=wal_dir, fsync_policy="always")
        for i in range(10):
            log.append(
                RawEvent(
                    source="BINANCE",
                    payload={
                        "instrument": "BTC-USDT",
                        "price": 65000.0 + i,
                        "exchange_ts": 1729000000.0 + i,
                        "sequence": i,
                    },
                )
            )
        log.close()

        # Invoke CLI cmd_replay with --from-wal and --verify-equivalence
        args = argparse.Namespace(
            from_wal=wal_dir,
            verify_equivalence=True,
            iterations=2,
            limit=None,
            speed=None,
            from_sqlite=None,
            from_journal=None,
            symbol=None,
            json=True,
        )

        cmd_replay(args)
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["is_equivalent"] is True
        assert data["events_replayed"] == 10
        assert data["iterations"] == 2
        assert len(data["state_hash"]) == 64
        assert len(data["events_hash"]) == 64
