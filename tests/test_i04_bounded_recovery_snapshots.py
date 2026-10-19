"""Acceptance tests for MDRAP Program I-4: Bounded Fast Recovery & Snapshot Cadence.

Verifies:
1. Version 2 snapshot format with cryptographic SHA-256 integrity validation.
2. Tamper-resistance: tampered snapshots safely trigger automatic fallback to full WAL replay.
3. Event-cadence snapshot automation (e.g. every N events).
4. Time-cadence snapshot automation (e.g. every T seconds).
5. Accurate telemetry in RecoveryMetrics (snapshot_used, snapshot_offset, recovery_duration_ms).
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from mdrap.clock import FixedClock
from mdrap.engine import Engine, EngineConfig
from mdrap.models import RawEvent


def test_i04_sha256_cryptographic_integrity_verification() -> None:
    """Version 2 snapshot saves SHA-256 digest; tampering triggers fallback to WAL replay."""
    clock = FixedClock(1700000000.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal"
        cfg = EngineConfig(
            wal_dir=str(wal_path), clock=clock, staleness_threshold_s=10.0
        )
        engine = Engine(cfg)

        events = [
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 150.0 + i,
                    "sequence": i,
                },
                receive_timestamp=1700000000.0 + i,
            )
            for i in range(1, 51)
        ]
        engine.submit(events)
        res = engine.save_snapshot()
        assert res["snapshot_offset"] == 49
        assert "sha256" in res
        snap_path = Path(res["path"])
        assert snap_path.exists()

        # Verify snapshot file has version 2 and valid sha256
        with open(snap_path, "r", encoding="utf-8") as f:
            snap_data = json.load(f)
        assert snap_data["version"] == 2
        assert snap_data["sha256"] == res["sha256"]
        engine.close()

        # Reopen clean engine -> snapshot should be used
        engine_clean = Engine(cfg)
        assert engine_clean.recovery_metrics.snapshot_used is True
        assert engine_clean.recovery_metrics.snapshot_offset == 49
        assert (
            engine_clean.recovery_metrics.replayed_records == 0
        )  # 0 records replayed since snap was at head
        assert engine_clean.state.event_count == 50
        engine_clean.close()

        # Tamper with snapshot file state JSON (flip a counter)
        with open(snap_path, "r", encoding="utf-8") as f:
            corrupt_data = json.load(f)
        corrupt_data["state"]["counts"]["VALID"] = 999999
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(corrupt_data, f)

        # Reopen with tampered snapshot: should reject snapshot, log warning, and fall back to full WAL replay
        engine_fallback = Engine(cfg)
        assert engine_fallback.recovery_metrics.snapshot_used is False
        assert engine_fallback.recovery_metrics.snapshot_offset == -1
        assert engine_fallback.recovery_metrics.replayed_records == 50
        assert engine_fallback.state.event_count == 50
        assert engine_fallback.state.counts["VALID"] == 50
        engine_fallback.close()


def test_i04_automatic_cadence_events() -> None:
    """Engine automatically triggers snapshots when event count crosses snapshot_cadence_events."""
    clock = FixedClock(1700000000.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal"
        cfg = EngineConfig(
            wal_dir=str(wal_path),
            clock=clock,
            staleness_threshold_s=10.0,
            snapshot_cadence_events=30,  # snapshot every 30 events
            snapshot_cadence_seconds=0.0,
        )
        engine = Engine(cfg)

        snap_file = wal_path / "snapshot.json"
        assert not snap_file.exists()

        # Submit batch of 20 events (under threshold)
        b1 = [
            RawEvent(
                source="BATS",
                payload={
                    "instrument": "MSFT",
                    "event_type": "TRADE",
                    "price": 300.0 + i,
                    "sequence": i,
                },
                receive_timestamp=1700000000.0 + i,
            )
            for i in range(1, 21)
        ]
        engine.submit(b1)
        assert not snap_file.exists()

        # Submit batch of 20 more events (total 40 >= 30: triggers snapshot!)
        b2 = [
            RawEvent(
                source="BATS",
                payload={
                    "instrument": "MSFT",
                    "event_type": "TRADE",
                    "price": 320.0 + i,
                    "sequence": 20 + i,
                },
                receive_timestamp=1700000020.0 + i,
            )
            for i in range(1, 21)
        ]
        engine.submit(b2)
        assert snap_file.exists()

        # Submit batch of 10 more events (total 50)
        b3 = [
            RawEvent(
                source="BATS",
                payload={
                    "instrument": "MSFT",
                    "event_type": "TRADE",
                    "price": 340.0 + i,
                    "sequence": 40 + i,
                },
                receive_timestamp=1700000040.0 + i,
            )
            for i in range(1, 11)
        ]
        engine.submit(b3)
        engine.close()

        # Reopen: should use snapshot from offset 39 and replay only the remaining 10 events (offset 40..49)
        engine_recovered = Engine(cfg)
        assert engine_recovered.recovery_metrics.snapshot_used is True
        assert engine_recovered.recovery_metrics.snapshot_offset == 39
        assert engine_recovered.recovery_metrics.replayed_records == 10
        assert engine_recovered.state.event_count == 50
        engine_recovered.close()


def test_i04_automatic_cadence_time() -> None:
    """Engine automatically triggers snapshots when elapsed wall time crosses snapshot_cadence_seconds."""
    clock = FixedClock(1700000000.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal"
        cfg = EngineConfig(
            wal_dir=str(wal_path),
            clock=clock,
            staleness_threshold_s=10.0,
            snapshot_cadence_events=0,  # disable event count cadence
            snapshot_cadence_seconds=0.04,  # snapshot every 40 ms
        )
        engine = Engine(cfg)
        snap_file = wal_path / "snapshot.json"
        assert not snap_file.exists()

        # Initial submit
        ev1 = RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5000.0,
                "sequence": 1,
            },
            receive_timestamp=1700000000.0,
        )
        engine.submit(ev1)
        assert not snap_file.exists()

        # Sleep past 40ms threshold
        time.sleep(0.06)

        # Next submit crosses time cadence threshold
        ev2 = RawEvent(
            source="CME",
            payload={
                "instrument": "ES",
                "event_type": "TRADE",
                "price": 5001.0,
                "sequence": 2,
            },
            receive_timestamp=1700000001.0,
        )
        engine.submit(ev2)
        assert snap_file.exists()
        engine.close()


def test_i04_recovery_telemetry_metrics() -> None:
    """RecoveryMetrics accurately measures duration and record classification on startup."""
    clock = FixedClock(1700000000.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal"
        cfg = EngineConfig(
            wal_dir=str(wal_path), clock=clock, staleness_threshold_s=10.0
        )
        engine = Engine(cfg)

        # 30 valid trades + 5 invalid crossed quotes
        events = [
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "price": 150.0 + i,
                    "sequence": i,
                },
                receive_timestamp=1700000000.0 + i,
            )
            for i in range(1, 31)
        ] + [
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "QUOTE",
                    "bid": 160.0,
                    "ask": 150.0,
                    "sequence": 30 + i,
                },
                receive_timestamp=1700000030.0 + i,
            )
            for i in range(1, 6)
        ]
        engine.submit(events)
        engine.close()

        # Reopen without snapshot (full WAL replay)
        engine_reopened = Engine(cfg)
        metrics = engine_reopened.recovery_metrics
        assert metrics.snapshot_used is False
        assert metrics.replayed_records == 35
        assert metrics.valid_records == 30
        assert metrics.quarantined_records == 5
        assert metrics.recovery_duration_ms > 0.0
        engine_reopened.close()
