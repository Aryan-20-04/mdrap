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
        "source": "TEST",
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


def test_n1_live_equals_replay_property(tmp_path):
    """N1 Property Test: Live submission IDs and states must match WAL replay bit-for-bit."""
    import random
    rng = random.Random(42)
    wal_dir = tmp_path / "n1_prop_wal"
    clock = FixedClock(1700000000.0)

    engine = Engine.open(wal_dir, config={"clock": clock, "staleness_threshold_s": 10.0})

    sources = ["BINANCE", "KRAKEN", "COINBASE", "BYBIT"]
    instruments = ["BTC/USD", "ETH/USD", "SOL/USD"]
    raw_events = []

    for i in range(50):
        src = rng.choice(sources)
        inst = rng.choice(instruments)
        price = round(rng.uniform(10.0, 50000.0), 2)
        qty = round(rng.uniform(0.1, 10.0), 4)
        ts = 1700000000.0 + i * 0.1
        # Unsequenced event (no sequence number, no explicit raw_id)
        raw = RawEvent(
            source=src,
            payload={
                "instrument": inst,
                "event_type": "TRADE",
                "price": price,
                "quantity": qty,
                "exchange_ts": ts,
            },
            receive_timestamp=ts,
        )
        raw_events.append(raw)

    live_decisions = []
    for raw in raw_events:
        dec = engine.submit(raw)
        live_decisions.append(dec)

    live_counts = dict(engine.state.counts)
    live_event_count = engine.state.event_count
    engine.close()

    # Reopen and replay
    engine_replay = Engine.open(wal_dir, config={"clock": clock, "staleness_threshold_s": 10.0})
    replay_decisions = engine_replay.replay(0)

    assert len(replay_decisions) == len(live_decisions)
    assert engine_replay.state.event_count == live_event_count
    assert engine_replay.state.counts == live_counts

    for live_dec, rep_dec in zip(live_decisions, replay_decisions):
        assert live_dec.event_id == rep_dec.event_id, f"ID mismatch: live={live_dec.event_id}, rep={rep_dec.event_id}"
        assert live_dec.quality_status == rep_dec.quality_status
        assert live_dec.reasons == rep_dec.reasons
        if live_dec.canonical_event and rep_dec.canonical_event:
            assert live_dec.canonical_event.event_id == rep_dec.canonical_event.event_id
            assert live_dec.canonical_event.raw_id == rep_dec.canonical_event.raw_id

    engine_replay.close()


def test_n3_state_consistency_on_step_exception(monkeypatch):
    """N3: Unhandled step exception rolls back state, increments event_count exactly once, and logs safely."""
    engine = Engine()
    state = engine.create_initial_state()
    clock = FixedClock(1700000000.0)

    raw = RawEvent(
        source="BINANCE",
        payload={"instrument": "BTC/USD", "event_type": "TRADE", "price": 100.0, "quantity": 1.0, "exchange_ts": 1700000000.0},
        receive_timestamp=1700000000.0,
    )

    # First event normal
    state, dec1 = engine.step(state, raw, clock)
    assert state.event_count == 1
    assert state.counts["VALID"] == 1
    assert state.counts["INVALID"] == 0

    # Force an unhandled exception inside _step_internal
    def faulty_step(*args, **kwargs):
        raise RuntimeError("Simulated unhandled step disaster")

    monkeypatch.setattr(engine, "_step_internal", faulty_step)

    raw_fail = RawEvent(
        source="BINANCE",
        payload={"instrument": "ETH/USD", "event_type": "TRADE", "price": 200.0, "quantity": 1.0, "exchange_ts": 1700000000.0},
        receive_timestamp=1700000000.0,
    )

    state_after, dec2 = engine.step(state, raw_fail, clock)
    # Event count must be exactly 2 (prev 1 + 1), NOT 3 (no double count!)
    assert state_after.event_count == 2
    assert state_after.counts["INVALID"] == 1
    assert state_after.counts["VALID"] == 1
    assert dec2.quality_status == QualityStatus.INVALID
    assert dec2.quarantine_row is not None


def test_n4_max_payload_bytes_boundary(tmp_path):
    """N4: Engine.submit() intercepts oversized payloads at boundary without stalling WAL."""
    wal_dir = tmp_path / "n4_wal"
    engine = Engine.open(wal_dir, config={"max_payload_bytes": 500})

    # Event within size limit
    normal_raw = RawEvent(
        source="KRAKEN",
        payload={"instrument": "BTC/USD", "event_type": "TRADE", "price": 50000.0, "quantity": 1.0, "exchange_ts": 1700000000.0},
        receive_timestamp=1700000000.0,
    )
    dec_ok = engine.submit(normal_raw)
    assert dec_ok.quality_status == QualityStatus.VALID

    # Oversized payload (> 500 bytes)
    huge_data = "X" * 1000
    huge_raw = RawEvent(
        source="KRAKEN",
        payload={"instrument": "BTC/USD", "event_type": "TRADE", "junk": huge_data},
        receive_timestamp=1700000000.0,
    )
    dec_huge = engine.submit(huge_raw)
    assert dec_huge.quality_status == QualityStatus.INVALID
    assert dec_huge.quarantine_row is not None
    assert Reason.SCHEMA_VIOLATION.value in dec_huge.reasons

    # Check WAL didn't record huge payload
    engine.close()
    log = IngestLog(str(wal_dir))
    records = list(log.iter_from(0))
    # Only 1 record should be in WAL, because the oversized one was intercepted before log append
    assert len(records) == 1
    log.close()


def test_m1_raw_archive_path_traversal(tmp_path):
    """M1: RawArchive strictly sanitizes source identifiers and prevents path traversal."""
    from mdrap.archive import RawArchive

    archive_dir = tmp_path / "archive"
    archive = RawArchive(str(archive_dir))

    # Path traversal attempts
    traversal_sources = [
        "../../etc/passwd",
        "../escape",
        "..\\windows\\system32",
        "foo/bar",
        "foo\\bar",
        "/absolute/escape",
    ]

    for bad_src in traversal_sources:
        archive.write(RawEvent(source=bad_src, payload={"test": 1}, receive_timestamp=1700000000.0))

    archive.flush()
    archive.close()

    # Verify that NO files were created outside archive_dir
    created_files = list(archive_dir.rglob("*.jsonl"))
    assert len(created_files) > 0
    for f in created_files:
        assert str(f.resolve()).startswith(str(archive_dir.resolve())), f"File escaped archive dir: {f}"
        # Ensure filenames do not contain path traversal components
        assert ".." not in f.name
        assert "/" not in f.name
        assert "\\" not in f.name


def test_cb_ingestlog_lock_rejection_on_concurrent_open(tmp_path):
    """C-B: IngestLog enforces exclusive process lock against concurrent openers."""
    from mdrap.ingestlog import IngestLogLockedError

    wal_dir = tmp_path / "lock_wal"
    log1 = IngestLog(str(wal_dir))

    # Second opener must be rejected with IngestLogLockedError
    with pytest.raises(IngestLogLockedError, match="locked by another process"):
        IngestLog(str(wal_dir))

    # Closing log1 allows log2 to open successfully
    log1.close()
    log2 = IngestLog(str(wal_dir))
    log2.append(RawEvent(source="TEST", payload={"test": 1}))
    assert log2.next_offset == 1
    log2.close()


def test_cb_ingestlog_salvage_utility(tmp_path):
    """C-B: IngestLog.salvage() recovers intact frames from corrupted segments and creates clean backups."""
    wal_dir = tmp_path / "salvage_wal"
    log = IngestLog(str(wal_dir))
    for i in range(10):
        log.append(RawEvent(source="NASDAQ", payload={"instrument": "AAPL", "price": 150.0 + i, "seq": i}))
    log.close()

    # Corrupt payload byte of record 5 in middle of segment
    seg_files = list(wal_dir.glob("segment_*.log"))
    assert len(seg_files) == 1
    seg_path = seg_files[0]

    with open(seg_path, "r+b") as f:
        # Seek into payload of record 5
        f.seek(32 + (28 + 50) * 4 + 35)
        b = f.read(1)
        f.seek(-1, os.SEEK_CUR)
        f.write(bytes([b[0] ^ 0xFF]))

    # Opening must raise IngestLogCorruptError
    with pytest.raises(IngestLogCorruptError):
        IngestLog(str(wal_dir))

    # Run salvage
    salvage_report = IngestLog.salvage(str(wal_dir), backup=True)
    assert salvage_report["scanned_segments"] == 1
    assert salvage_report["corrupted_segments"] == 1
    assert salvage_report["recovered_records"] >= 9
    assert len(list(wal_dir.glob("*.bak*"))) == 1

    # Reopening salvaged log succeeds cleanly!
    log_repaired = IngestLog(str(wal_dir))
    repaired_events = list(log_repaired.iter_from(0))
    assert len(repaired_events) >= 9
    log_repaired.close()



