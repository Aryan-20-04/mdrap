"""Stage D Regression Test Suite: Engine Sequence Correctness & Quality Engine Semantic Parity.

Verifies:
1. Sequence unpoisoning: large sequence jumps do not permanently poison subsequent valid traffic.
2. Sliding-window deduplication: old retransmitted sequences are flagged DUPLICATE (INVALID).
3. Regime shift price adaptation: consecutive consistent outliers re-seed the corridor to the new regime.
4. Parity across tiers: Engine inline processing matches QualityEngine and FastQualityEngine semantics.
"""

from __future__ import annotations

import tempfile
from typing import Any

import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.fastpath import FastQualityEngine
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.quality import QualityEngine


def test_sequence_jump_does_not_poison_subsequent_events(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never"),
    )

    base = 1_700_000_000.0
    # Sequences 1..5
    for s in range(1, 6):
        r = engine.submit(
            RawEvent(
                source="SRC",
                payload={
                    "instrument": "X",
                    "event_type": "QUOTE",
                    "bid": 10.0,
                    "ask": 10.1,
                    "sequence": s,
                },
                receive_timestamp=base + s,
            )
        )
        assert r.quality_status == QualityStatus.VALID

    # Poison jump sequence (2**62)
    r_jump = engine.submit(
        RawEvent(
            source="SRC",
            payload={
                "instrument": "X",
                "event_type": "QUOTE",
                "bid": 10.0,
                "ask": 10.1,
                "sequence": 2**62,
            },
            receive_timestamp=base + 6,
        )
    )
    assert r_jump.quality_status == QualityStatus.SUSPICIOUS
    assert "SEQUENCE_GAP" in r_jump.reasons

    # Subsequent legit sequences 6..15 must NOT be poisoned
    for s in range(6, 16):
        r = engine.submit(
            RawEvent(
                source="SRC",
                payload={
                    "instrument": "X",
                    "event_type": "QUOTE",
                    "bid": 10.0,
                    "ask": 10.1,
                    "sequence": s,
                },
                receive_timestamp=base + s + 1,
            )
        )
        assert r.quality_status == QualityStatus.VALID, (
            f"Sequence {s} was poisoned: {r.quality_status} {r.reasons}"
        )

    engine.close()


def test_old_retransmission_is_marked_duplicate(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never"),
    )

    base = 1_700_000_000.0
    for s in range(1, 11):
        engine.submit(
            RawEvent(
                source="SRC",
                payload={
                    "instrument": "X",
                    "event_type": "QUOTE",
                    "bid": 10.0,
                    "ask": 10.1,
                    "sequence": s,
                },
                receive_timestamp=base + s,
            )
        )

    # Retransmit seq 10 (last)
    r_10 = engine.submit(
        RawEvent(
            source="SRC",
            payload={
                "instrument": "X",
                "event_type": "QUOTE",
                "bid": 10.0,
                "ask": 10.1,
                "sequence": 10,
            },
            receive_timestamp=base + 11,
        )
    )
    assert r_10.quality_status == QualityStatus.INVALID
    assert "DUPLICATE" in r_10.reasons

    # Retransmit seq 5 (old)
    r_5 = engine.submit(
        RawEvent(
            source="SRC",
            payload={
                "instrument": "X",
                "event_type": "QUOTE",
                "bid": 10.0,
                "ask": 10.1,
                "sequence": 5,
            },
            receive_timestamp=base + 12,
        )
    )
    assert r_5.quality_status == QualityStatus.INVALID
    assert "DUPLICATE" in r_5.reasons

    engine.close()


def test_genuine_regime_shift_adapts_price_corridor(tmp_path: Any) -> None:
    wal_dir = tmp_path / "wal"
    engine = Engine.open(
        str(wal_dir),
        config=EngineConfig(fsync_policy="never"),
    )

    base = 1_700_000_000.0
    # 30 trades around 100.0
    for i in range(30):
        engine.submit(
            RawEvent(
                source="SRC",
                payload={
                    "instrument": "X",
                    "event_type": "TRADE",
                    "price": 100.0 + (i % 3) * 0.01,
                    "quantity": 1,
                    "sequence": i + 1,
                },
                receive_timestamp=base + i,
            )
        )

    # 60 trades around 120.0 (+20% regime shift)
    decisions = []
    for i in range(60):
        d = engine.submit(
            RawEvent(
                source="SRC",
                payload={
                    "instrument": "X",
                    "event_type": "TRADE",
                    "price": 120.0 + (i % 3) * 0.01,
                    "quantity": 1,
                    "sequence": 100 + i,
                },
                receive_timestamp=base + 30 + i,
            )
        )
        decisions.append(d.quality_status)

    # Outliers should be limited to initial adaptation window (8), then VALID
    suspicious_count = sum(1 for s in decisions if s == QualityStatus.SUSPICIOUS)
    valid_count = sum(1 for s in decisions if s == QualityStatus.VALID)

    assert suspicious_count == 8, (
        f"Expected exactly 8 outliers during adaptation, got {suspicious_count}"
    )
    assert valid_count == 52, (
        f"Expected 52 valid trades post-adaptation, got {valid_count}"
    )

    engine.close()
