"""Targeted Regression & Remediation Tests for Audit Findings (C1-C4 and High/Medium findings).

Validates:
- C1: Poison pill (10**400) safely quarantined as INVALID; Engine log replay succeeds without crashing.
- C2: Event without receive_timestamp deterministically replays with the same status as live submit.
- C3: Torn segment file (< 32 bytes) is cleaned up during recovery; corrupted frame magic raises IngestLogCorruptError.
- C4: Multi-source / multi-quantity unsequenced events avoid false duplicate collisions.
- Reason registry prevents arbitrary dynamic DDoS pollution.
- Multi-asset/derivatives fields round-trip losslessly in CanonicalEvent.
- String reason formatting is normalized properly.
- Safe parser rejects empty market events.
- Storage durability mode strictly validates arguments.
"""

from __future__ import annotations

import json
import math
import os
import struct
import zlib
import pytest

from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
    safe_parse_market_event,
)
from mdrap.clock import FixedClock
from mdrap.engine import Engine, EngineState
from mdrap.ingestlog import IngestLog, IngestLogCorruptError, LOG_MAGIC, FRAME_MAGIC
from mdrap.projection import SQLiteProjection
from mdrap.storage import Store


def test_c1_poison_pill_does_not_brick_engine(tmp_path):
    """C1: Number exceeding 1024 bits (10**400) is quarantined as INVALID and replay recovers cleanly."""
    wal_dir = tmp_path / "c1_wal"
    db_file = tmp_path / "c1.db"

    # 1. First run: submit 10**400 poison event
    engine = Engine.open(wal_dir, config={"db_path": str(db_file)})
    poison_raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 10**400,
            "quantity": 1.0,
        },
    )
    decision = engine.submit(poison_raw)
    assert decision.quality_status == QualityStatus.INVALID
    assert decision.canonical_event is None
    assert decision.quarantine_row is not None
    engine.close()

    # 2. Re-open engine: startup replay must NOT raise OverflowError or brick the engine
    engine2 = Engine.open(wal_dir, config={"db_path": str(db_file)})
    assert engine2.state.counts["INVALID"] == 1
    assert engine2.state.counts["VALID"] == 0

    # Submit valid event to ensure instance is fully functional
    valid_raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 50000.0,
            "quantity": 1.0,
        },
    )
    dec2 = engine2.submit(valid_raw)
    assert dec2.quality_status == QualityStatus.VALID
    assert dec2.canonical_event is not None
    engine2.close()


def test_c2_deterministic_replay_unstamped_timestamp(tmp_path):
    """C2: An event submitted without receive_timestamp is stamped before log append and replays identically."""
    wal_dir = tmp_path / "c2_wal"
    clock = FixedClock(1700000000.0)

    engine = Engine.open(wal_dir, config={"clock": clock, "staleness_threshold_s": 0.05})
    raw = RawEvent(
        source="FEEDX",
        payload={
            "instrument": "ETH/USD",
            "event_type": "TRADE",
            "price": 3000.0,
            "quantity": 2.0,
            "exchange_ts": 1700000000.0,
        },
        receive_timestamp=0.0,  # Unstamped
    )

    decision_live = engine.submit(raw)
    assert decision_live.quality_status == QualityStatus.VALID
    assert Reason.STALE.value not in decision_live.reasons
    engine.close()

    # Advance clock far into the future (e.g. 1 hour later)
    replay_clock = FixedClock(1700003600.0)
    engine_replay = Engine.open(
        wal_dir, config={"clock": replay_clock, "staleness_threshold_s": 0.05}
    )
    # The replayed event must still be VALID and NOT marked STALE
    assert engine_replay.state.counts["VALID"] == 1
    assert engine_replay.state.counts["SUSPICIOUS"] == 0
    engine_replay.close()


def test_c3_torn_header_and_corrupt_frame_magic(tmp_path):
    """C3: Torn segment header (<32 bytes) is pruned; corrupted frame magic raises IngestLogCorruptError."""
    log_dir = tmp_path / "c3_log"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a torn segment file (< 32 bytes) with standard .log suffix
    torn_file = log_dir / "segment_000000000000.log"
    torn_file.write_bytes(b"MDRAPLOG\x01\x00\x00\x00")  # 12 bytes (< 32 bytes header)

    # Opening IngestLog should delete the torn segment without crashing or leaving a headerless file
    log = IngestLog(str(log_dir))
    assert not torn_file.exists() or torn_file.stat().st_size >= 32

    # Append 3 valid records
    for i in range(3):
        log.append(RawEvent(source="TEST", payload={"seq": i}))
    log.close()

    # Verify reopen works
    log2 = IngestLog(str(log_dir))
    events = list(log2.iter_from(0))
    assert len(events) == 3
    log2.close()

    # 2. Corrupt frame magic in an existing segment
    segments = [f for f in log_dir.glob("*.log")]
    assert len(segments) >= 1
    target_seg = segments[0]
    data = bytearray(target_seg.read_bytes())
    # Offset of first frame magic is 32 (header length)
    assert data[32:34] == struct.pack(">H", FRAME_MAGIC)
    # Corrupt first byte of frame magic
    data[32] = 0x00
    target_seg.write_bytes(data)

    # Opening or iterating corrupted segment must raise IngestLogCorruptError
    with pytest.raises(IngestLogCorruptError):
        IngestLog(str(log_dir))


def test_c4_multi_source_unsequenced_dedup_no_false_positive(tmp_path):
    """C4: Distinct sources or quantities at identical timestamp/price must not collide as DUPLICATE."""
    clock = FixedClock(1700000000.0)
    engine = Engine(clock=clock)

    # Source 1: BINANCE
    ev1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "SOL/USD",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "exchange_ts": 1700000000.0,
        },
    )
    # Source 2: COINBASE (same price, same ts, but different source)
    ev2 = RawEvent(
        source="COINBASE",
        payload={
            "instrument": "SOL/USD",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 10.0,
            "exchange_ts": 1700000000.0,
        },
    )
    # Source 3: BINANCE (same source and ts, but different quantity)
    ev3 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "SOL/USD",
            "event_type": "TRADE",
            "price": 150.0,
            "quantity": 25.0,
            "exchange_ts": 1700000000.0,
        },
    )

    dec1 = engine.submit(ev1)
    dec2 = engine.submit(ev2)
    dec3 = engine.submit(ev3)

    assert dec1.quality_status == QualityStatus.VALID
    assert dec2.quality_status == QualityStatus.VALID
    assert dec3.quality_status == QualityStatus.VALID
    assert engine.state.counts["INVALID"] == 0

    # True duplicate: exact same payload and source
    dec4 = engine.submit(ev1)
    assert dec4.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dec4.reasons


def test_reason_metaclass_lockdown():
    """Arbitrary dynamic attribute access on Reason must raise AttributeError."""
    assert Reason.DUPLICATE == "DUPLICATE"
    assert Reason.STALE == "STALE"

    with pytest.raises(AttributeError):
        _ = Reason.ATTACKER_INVENTED_CODE

    with pytest.raises(ValueError):
        _ = Reason("NON_EXISTENT_CODE")


def test_lossless_multi_asset_serialization():
    """All 13 options/bond/derivatives fields are preserved in CanonicalEvent to_dict/from_dict."""
    ev = CanonicalEvent(
        event_id="opt_1",
        instrument_id="AAPL260116C00200000",
        event_type=EventType.QUOTE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        processing_timestamp=1700000000.002,
        source="CBOE",
        sequence_number=1,
        strike=200.0,
        delta=0.45,
        gamma=0.03,
        expiry_date="2026-01-16",
        contract_size=100.0,
        underlying_id="AAPL",
        open_interest=5000.0,
        put_call="CALL",
        implied_vol=0.25,
        coupon=0.05,
        maturity_date="2030-01-01",
        yield_to_worst=0.048,
        duration=4.2,
    )

    d = ev.to_dict()
    assert d["strike"] == 200.0
    assert d["implied_vol"] == 0.25
    assert d["maturity_date"] == "2030-01-01"

    restored = CanonicalEvent.from_dict(d)
    assert restored.strike == 200.0
    assert restored.delta == 0.45
    assert restored.gamma == 0.03
    assert restored.expiry_date == "2026-01-16"
    assert restored.contract_size == 100.0
    assert restored.underlying_id == "AAPL"
    assert restored.open_interest == 5000.0
    assert restored.put_call == "CALL"
    assert restored.implied_vol == 0.25
    assert restored.coupon == 0.05
    assert restored.maturity_date == "2030-01-01"
    assert restored.yield_to_worst == 0.048
    assert restored.duration == 4.2


def test_string_reasons_normalization():
    """from_dict with reasons='STALE' should yield ['STALE'], not split characters."""
    d = {
        "event_id": "ev_str",
        "instrument_id": "SPY",
        "event_type": "TRADE",
        "exchange_timestamp": 1700000000.0,
        "receive_timestamp": 1700000000.001,
        "processing_timestamp": 1700000000.002,
        "source=" : "TEST",
        "reasons": "STALE",
    }
    ev = CanonicalEvent.from_dict(d)
    assert ev.reasons == ["STALE"]


def test_safe_parse_market_event_empty_payload():
    """Empty payload must not be marked VALID with 0.0 price."""
    ev, errors = safe_parse_market_event({})
    assert ev.quality_status == QualityStatus.INVALID
    assert any("instrument_id" in err for err in errors)
    assert any("price" in err for err in errors)
    assert any("quantity" in err for err in errors)


def test_store_durability_mode_validation(tmp_path):
    """Store raises ValueError on invalid durability mode."""
    db_path = str(tmp_path / "dur.db")
    with pytest.raises(ValueError, match="Invalid durability mode"):
        Store(db_path, durability="complaince")
