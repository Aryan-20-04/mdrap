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
import time
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


def test_cb_ingestlog_verify_utility(tmp_path):
    """C-B: IngestLog.verify() detects clean logs and pinpoints corrupted segments and frames."""
    from mdrap.ingestlog import IngestLog
    from mdrap.engine import Engine

    wal_dir = tmp_path / "verify_wal"
    log = IngestLog(str(wal_dir))
    for i in range(15):
        log.append(RawEvent(source="COINBASE", payload={"instrument": "BTC/USD", "price": 50000.0 + i, "seq": i}))
    log.close()

    # 1. Clean verification check
    clean_report = IngestLog.verify(str(wal_dir))
    assert clean_report["is_clean"] is True
    assert clean_report["total_segments"] == 1
    assert clean_report["valid_frames"] == 15
    assert clean_report["corrupted_frames"] == 0

    # Also check through Engine.verify
    engine_report = Engine.verify(str(wal_dir))
    assert engine_report["is_clean"] is True

    # 2. Corrupt a frame CRC in the segment
    seg_files = list(wal_dir.glob("segment_*.log"))
    assert len(seg_files) == 1
    with open(seg_files[0], "r+b") as f:
        f.seek(32 + (28 + 60) * 2 + 10)
        b = f.read(1)
        f.seek(-1, os.SEEK_CUR)
        f.write(bytes([b[0] ^ 0xFF]))

    corrupt_report = IngestLog.verify(str(wal_dir))
    assert corrupt_report["is_clean"] is False
    assert corrupt_report["corrupted_segments"] == 1
    assert corrupt_report["corrupted_frames"] >= 1
    assert len(corrupt_report["errors"]) >= 1


def test_cli_wal_verify_and_salvage_commands(tmp_path, capsys):
    """Phase 2: mdrap wal verify and salvage CLI subcommands operate accurately."""
    from mdrap.cli.operations import cmd_wal
    from mdrap.ingestlog import IngestLog
    import argparse

    wal_dir = tmp_path / "cli_wal"
    log = IngestLog(str(wal_dir))
    for i in range(5):
        log.append(RawEvent(source="KRAKEN", payload={"instrument": "ETH/USD", "price": 3000.0 + i, "seq": i}))
    log.close()

    # 1. Test verify command with JSON output
    args_verify = argparse.Namespace(
        action="verify",
        db=str(tmp_path / "dummy.db"),
        wal_path=str(wal_dir),
        json=True,
    )
    cmd_wal(args_verify)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["is_clean"] is True
    assert data["valid_frames"] == 5

    # 2. Test salvage command with JSON output
    args_salvage = argparse.Namespace(
        action="salvage",
        db=str(tmp_path / "dummy.db"),
        wal_path=str(wal_dir),
        backup=True,
        json=True,
    )
    cmd_wal(args_salvage)
    out_salvage = capsys.readouterr().out
    data_salvage = json.loads(out_salvage)
    assert data_salvage["scanned_segments"] == 1
    assert data_salvage["recovered_records"] == 5


def test_pipeline_glued_record_bug_prevention(tmp_path):
    """C-A: Incomplete crash fragments in journal do not fuse with fresh records on restart."""
    from mdrap.pipeline import Pipeline
    from mdrap.storage import Store

    db_path = str(tmp_path / "glued_test.db")
    jrn_path = f"{db_path}.journal"

    # Simulate an abrupt crash that leaves an incomplete fragment without trailing newline
    with open(jrn_path, "w", encoding="utf-8") as f:
        f.write('{"type": "canonical", "payload": {"event_id": "ev_1", "instrument_id": "BTC/USD"}}\n')
        f.write('{"type": "canonical", "payload": {"event_id": "ev_2"')  # Torn tail!

    store = Store(db_path)
    pipeline = Pipeline(store=store)

    # Append fresh record
    fresh_raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "ETH/USD",
            "event_type": "TRADE",
            "price": 2000.0,
            "quantity": 1.0,
            "sequence": 100,
            "exchange_ts": time.time(),
        },
    )
    ev = pipeline.process_one(fresh_raw)
    assert ev is not None
    assert ev.quality_status == QualityStatus.VALID
    pipeline.close()
    store.close()

    # Read lines of the journal file
    with open(jrn_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    # Line 1 is ev_1
    assert "ev_1" in lines[0]
    # Line 2 is the torn tail
    assert '{"type": "canonical", "payload": {"event_id": "ev_2"' == lines[1]
    # Line 3 is the newly written record on its OWN line, NOT fused into Line 2!
    assert len(lines) >= 3
    parsed_fresh = json.loads(lines[2])
    assert parsed_fresh["type"] == "canonical"
    assert parsed_fresh["payload"]["instrument_id"] == "ETH/USD"


def test_n2_unsequenced_business_id_dedup():
    """N2: Unsequenced events without sequence and exchange_ts dedupe on trade/message IDs."""
    from mdrap.engine import Engine

    engine = Engine()

    # Two exact retransmissions with missing sequence and missing exchange_ts
    raw1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "SOL/USD",
            "event_type": "TRADE",
            "trade_id": "trd_98765",
            "price": 140.50,
            "quantity": 5.0,
        },
    )
    raw2 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "SOL/USD",
            "event_type": "TRADE",
            "trade_id": "trd_98765",  # Exact duplicate business ID!
            "price": 140.50,
            "quantity": 5.0,
        },
    )

    dec1 = engine.submit(raw1)
    assert dec1.quality_status == QualityStatus.VALID

    # Second retransmit must be caught as DUPLICATE and quarantined as INVALID
    dec2 = engine.submit(raw2)
    assert dec2.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in dec2.reasons


def test_phase3_ingestlog_group_commit_batching(tmp_path):
    """Phase 3: append_batch() executes atomic group commit with single fsync per batch."""
    from mdrap.ingestlog import IngestLog
    from mdrap.engine import Engine

    wal_dir = tmp_path / "gc_wal"
    log = IngestLog(str(wal_dir), fsync_policy="always")

    # 1. Direct IngestLog append_batch
    batch_raw = [
        RawEvent(source="COINBASE", payload={"instrument": "BTC/USD", "price": 50000.0 + i, "seq": i})
        for i in range(50)
    ]
    offsets = log.append_batch(batch_raw)
    assert len(offsets) == 50
    assert offsets == list(range(50))
    assert log.next_offset == 50
    log.close()

    # Verify all 50 records replayed in exact order
    log_read = IngestLog(str(wal_dir))
    replayed = list(log_read.iter_from(0))
    assert len(replayed) == 50
    assert [off for off, _ in replayed] == list(range(50))
    log_read.close()

    # 2. Engine submit batch executes group commit through log
    eng_dir = tmp_path / "gc_engine"
    engine = Engine.open(str(eng_dir), config={"fsync_policy": "always"})
    decisions = engine.submit(batch_raw)
    assert isinstance(decisions, list)
    assert len(decisions) == 50
    assert [d.offset for d in decisions] == list(range(50))
    assert engine.state.event_count == 50
    engine.close()


def test_phase3_engine_snapshot_bounded_startup(tmp_path):
    """Phase 3: Engine snapshots enable O(tail) bounded startup without O(history) replay."""
    from mdrap.engine import Engine

    wal_dir = tmp_path / "snap_engine"
    engine = Engine.open(str(wal_dir))

    # Process initial 40 events
    events_1 = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0 + (i * 0.01),
                "quantity": 10.0,
                "sequence": i,
                "exchange_ts": 1000.0 + i,
            },
        )
        for i in range(40)
    ]
    engine.submit(events_1)
    assert engine.state.event_count == 40

    # Save snapshot
    snap_info = engine.save_snapshot()
    assert snap_info["snapshot_offset"] == 39
    assert os.path.exists(snap_info["path"])

    # Close engine
    engine.close()

    # Reopen on same WAL directory: must restore directly from snapshot.json
    engine_reopened = Engine.open(str(wal_dir))
    assert engine_reopened.state.event_count == 40
    assert engine_reopened.state.sequence_state["NASDAQ:AAPL"] == 39

    # Append 10 more events; offsets continue seamlessly from 40 to 49
    events_2 = [
        RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.40 + (i * 0.01),
                "quantity": 10.0,
                "sequence": 40 + i,
                "exchange_ts": 1040.0 + i,
            },
        )
        for i in range(10)
    ]
    decisions_2 = engine_reopened.submit(events_2)
    assert len(decisions_2) == 10
    assert [d.offset for d in decisions_2] == list(range(40, 50))
    assert engine_reopened.state.event_count == 50
    engine_reopened.close()


def test_phase4_from_dict_bad_input_and_nan_safety():
    """Phase 4: from_dict never raises on bad input and rejects NaN/inf without corrupting models."""
    import math
    from mdrap.models import CanonicalEvent, QualityStatus
    from mdrap.client import MarketEvent as ClientMarketEvent

    # 1. CanonicalEvent.from_dict with NaN price
    nan_data = {
        "event_id": "test_nan",
        "instrument_id": "AAPL",
        "price": float("nan"),
        "exchange_timestamp": float("nan"),
    }
    ev = CanonicalEvent.from_dict(nan_data)
    assert ev.price is None
    assert ev.exchange_timestamp == 0.0
    assert ev.quality_status == QualityStatus.INVALID
    assert "SCHEMA_VIOLATION" in ev.reasons

    # 2. CanonicalEvent.from_dict with invalid string price
    bad_data = {
        "event_id": "test_bad",
        "instrument_id": "AAPL",
        "price": "not_a_valid_float_price",
    }
    ev2 = CanonicalEvent.from_dict(bad_data)
    assert ev2.price is None
    assert ev2.quality_status == QualityStatus.INVALID

    # 3. ClientMarketEvent.from_dict with NaN price
    c_nan = ClientMarketEvent.from_dict({"sym": "BTC/USD", "price": float("nan"), "status": "VALID"})
    assert c_nan.price is None
    assert c_nan.status == "INVALID"

    # 4. ClientMarketEvent.from_dict with string price
    c_bad = ClientMarketEvent.from_dict({"sym": "BTC/USD", "price": "corrupt_data", "exchange_ts": "bad_ts"})
    assert c_bad.price is None
    assert c_bad.exchange_ts == 0.0
    assert c_bad.status == "INVALID"


def test_phase4_market_event_name_collision_resolved():
    """Phase 4: mdrap exports models.MarketEvent as MarketEvent and client.MarketEvent as ClientMarketEvent."""
    import mdrap
    from mdrap.models import MarketEvent as CoreMarketEvent
    from mdrap.client import MarketEvent as ClientStreamEvent

    # mdrap.MarketEvent is the core models.MarketEvent
    assert mdrap.MarketEvent is CoreMarketEvent
    # mdrap.ClientMarketEvent is the client SDK streaming event
    assert mdrap.ClientMarketEvent is ClientStreamEvent
    assert mdrap.ClientMarketEvent is not mdrap.MarketEvent


def test_phase4_hashedkeystore_legacy_unsalted_migration():
    """Phase 4: HashedKeyStore transparently migrates legacy unsalted SHA-256 tokens to salted hashes."""
    import hashlib
    from mdrap.security import HashedKeyStore, ClientEntitlement, Role, hash_api_key

    keystore = HashedKeyStore()
    raw_token = "legacy_unsalted_test_token_123"
    legacy_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    ent = ClientEntitlement(
        token_hash=legacy_hash,
        client_id="LegacyClient",
        key_prefix=raw_token[:12] + "...",
        key_id=legacy_hash[:16],
        role=Role.OPERATOR,
    )
    # Store with legacy unsalted hash directly (simulating pre-v3.0.0 database)
    dict.__setitem__(keystore, legacy_hash, ent)
    assert legacy_hash in dict.keys(keystore)

    # Lookup by token: must find, return, and seamlessly migrate to salted hash
    retrieved = keystore.get_by_token(raw_token)
    assert retrieved is not None
    assert retrieved.client_id == "LegacyClient"

    salted_hash = hash_api_key(raw_token)
    assert salted_hash in dict.keys(keystore)
    assert legacy_hash not in dict.keys(keystore)
    assert retrieved.token_hash == salted_hash


def test_phase4_api_ingest_durable_engine_wal_path(tmp_path):
    """Phase 4: /v1/ingest persists incoming events directly to Engine IngestLog WAL before returning 200 OK."""
    from starlette.testclient import TestClient
    from mdrap.api import create_app, AppState
    from mdrap.storage import Store
    from mdrap.security import SecurityManager
    from mdrap.ingestlog import IngestLog

    db_file = tmp_path / "api_test.db"
    wal_dir = tmp_path / "api_test.wal"

    store = Store(str(db_file))
    sec = SecurityManager(store=store)
    op_ent = sec.register_api_key(client_id="OpClient", role="OPERATOR")
    token = op_ent.token

    state = AppState(db_path=str(db_file), store=store, security_manager=sec, wal_path=str(wal_dir))
    app = create_app(state=state)
    client = TestClient(app)

    # Ingest event via API
    payload = {
        "source": "COINBASE",
        "payload": {
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "price": 62500.0,
            "quantity": 0.5,
            "sequence": 1,
            "exchange_ts": time.time(),
        },
    }
    r = client.post("/v1/ingest", json=payload, headers={"X-API-Key": token})
    assert r.status_code == 200
    res_data = r.json()
    assert res_data["status"] == "ok"
    assert res_data["ingested"] == 1
    assert len(res_data["canonical"]) == 1
    assert res_data["canonical"][0]["price"] == 62500.0

    # Verify event was durably committed to Engine WAL on disk
    assert state.engine is not None
    assert state.engine.log is not None
    # Close engine to release advisory process lock before opening inspection log
    state.engine.close()
    wal_records = list(IngestLog(str(wal_dir)).iter_from(0))
    assert len(wal_records) >= 1
    assert wal_records[0][1].source == "COINBASE"
    assert wal_records[0][1].payload["price"] == 62500.0

    store.close()


def test_phase4_version_discipline_metadata():
    """Phase 4: Version metadata exposes commit hash, segment version, and stability policy."""
    import mdrap._version as v

    assert v.__version__ == "3.1.0"
    assert v.__segment_version__ == 1
    assert v.__journal_version__ == 1
    assert hasattr(v, "__commit__")
    assert isinstance(v.__commit__, str)
    assert len(v.__commit__) > 0
    assert "Semantic Versioning" in v.__stability_policy__


def test_granular_entitlements_venue_and_symbol_licensing(tmp_path):
    """Verify granular client licensing enforcement at the security and store tiers."""
    from mdrap.security import SecurityManager, Role, AccessDenied
    from mdrap.storage import Store

    db_path = str(tmp_path / "licensing.db")
    store = Store(db_path)
    sec = SecurityManager(store=store)

    ent = sec.register_api_key(
        client_id="LicensedHedgeFund",
        role=Role.OPERATOR,
        allowed_sources=["BINANCE", "KRAKEN"],
        allowed_symbols=["BTC/USD", "ETH-USD"],
    )

    # 1. Direct authorization checks
    # Valid source & symbol
    sec.authorize(ent, Role.OPERATOR, action_name="feed_ingest", source="BINANCE", symbol="BTC/USD")
    sec.authorize(ent, Role.OPERATOR, action_name="feed_ingest", source="KRAKEN", symbol="BTCUSD")  # Normalized
    sec.authorize(ent, Role.OPERATOR, action_name="feed_ingest", source="BINANCE", symbol="ETHUSD")  # Normalized

    # Unlicensed source
    with pytest.raises(AccessDenied) as exc:
        sec.authorize(ent, Role.OPERATOR, action_name="feed_ingest", source="COINBASE", symbol="BTC/USD")
    assert "feed source 'COINBASE'" in str(exc.value)

    # Unlicensed symbol
    with pytest.raises(AccessDenied) as exc:
        sec.authorize(ent, Role.OPERATOR, action_name="feed_ingest", source="BINANCE", symbol="SOL/USD")
    assert "instrument 'SOL/USD'" in str(exc.value)

    # 2. Store persistence and roundtrip
    store.commit()
    loaded_keys = store.load_api_keys()
    matching = [k for k in loaded_keys if k.client_id == "LicensedHedgeFund"]
    assert len(matching) == 1
    loaded_ent = matching[0]
    assert loaded_ent.allowed_sources == ["BINANCE", "KRAKEN"]
    assert loaded_ent.allowed_symbols == ["BTC/USD", "ETH-USD"]

    store.close()


def test_granular_entitlements_rest_and_websocket_enforcement(tmp_path):
    """Verify REST and WebSocket enforcement of granular symbol and source licensing."""
    import asyncio
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from mdrap.api import AppState, create_app
    from mdrap.security import SecurityManager, Role
    from mdrap.storage import Store

    db_path = str(tmp_path / "api_licensing.db")
    store = Store(db_path)
    sec = SecurityManager(store=store)

    licensed_ent = sec.register_api_key(
        client_id="RestrictedTrader",
        role=Role.OPERATOR,
        allowed_sources=["BINANCE"],
        allowed_symbols=["BTC/USD"],
    )
    store.commit()

    state = AppState(db_path=db_path, store=store, security_manager=sec)
    app = create_app(state=state)
    client = TestClient(app)

    token = licensed_ent.token
    headers = {"X-API-Key": token}

    # 1. Ingest with unauthorized source -> 403 Forbidden
    resp = client.post(
        "/v1/ingest",
        json={"source": "COINBASE", "payload": {"instrument": "BTC/USD", "price": 50000.0, "quantity": 1.0}},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "not authorized to ingest for source 'COINBASE'" in resp.json()["detail"]

    # 2. Ingest with unauthorized symbol -> 403 Forbidden
    resp = client.post(
        "/v1/ingest",
        json={"source": "BINANCE", "payload": {"instrument": "ETH/USD", "price": 3000.0, "quantity": 1.0}},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "not authorized to ingest for instrument 'ETH/USD'" in resp.json()["detail"]

    # 3. Ingest with authorized source and symbol -> 200 OK
    resp = client.post(
        "/v1/ingest",
        json={"source": "BINANCE", "payload": {"instrument": "BTC/USD", "price": 60000.0, "quantity": 1.0, "sequence": 1, "exchange_ts": time.time()}},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 1

    # 4. Market queries: BBO and Depth for unlicensed symbol -> 403 Forbidden
    resp_bbo = client.get("/v1/bbo/ETH/USD", headers=headers)
    assert resp_bbo.status_code == 403

    resp_depth = client.get("/v1/depth/ETH/USD", headers=headers)
    assert resp_depth.status_code == 403

    # 5. WebSocket licensing enforcement
    with client.websocket_connect("/v1/events/stream", headers={"X-API-Key": token}) as ws:
        ack = ws.receive_json()
        assert ack["type"] == "ACK"

        # Try to subscribe to unlicensed symbol -> receives ERROR
        ws.send_json({"action": "SUB", "symbols": ["ETH/USD"]})
        err_msg = ws.receive_json()
        assert err_msg["type"] == "ERROR"
        assert "Access denied: Not licensed for instrument(s): ETH/USD" in err_msg["error"]

        # Subscribe to licensed symbol -> receives update
        ws.send_json({"action": "SUB", "symbols": ["BTC/USD"]})
        sub_msg = ws.receive_json()
        assert sub_msg["type"] == "SUBSCRIPTION_UPDATE"
        assert "BTC/USD" in sub_msg["subscribed"]

        # Broadcast unlicensed instrument event -> subscriber does not receive it
        asyncio.run(state.broadcast_event({
            "type": "CANONICAL_TICK",
            "instrument_id": "ETH/USD",
            "price": 3100.0,
            "source": "BINANCE",
        }))

        # Broadcast unlicensed source event -> subscriber does not receive it
        asyncio.run(state.broadcast_event({
            "type": "CANONICAL_TICK",
            "instrument_id": "BTC/USD",
            "price": 60100.0,
            "source": "COINBASE",
        }))

        # Broadcast licensed event -> subscriber receives it
        asyncio.run(state.broadcast_event({
            "type": "CANONICAL_TICK",
            "instrument_id": "BTC/USD",
            "price": 60200.0,
            "source": "BINANCE",
        }))

        received = ws.receive_json()
        assert received["type"] == "CANONICAL_TICK"
        assert received["instrument_id"] == "BTC/USD"
        assert received["price"] == 60200.0
        assert received["source"] == "BINANCE"

    store.close()


def test_financial_fixed_point_and_decimal_precision():
    """Verify fixed-point conversion and Decimal conversion accuracy and NaN/inf guards."""
    from decimal import Decimal
    from mdrap.models import (
        to_fixed_point_price,
        from_fixed_point_price,
        to_decimal_price,
        DEFAULT_PRICE_SCALE,
    )

    # 1. Fixed point price conversion
    price_flt = 65432.12345678
    scaled = to_fixed_point_price(price_flt)
    assert scaled == 6543212345678
    assert from_fixed_point_price(scaled) == pytest.approx(price_flt, abs=1e-8)

    # String input
    assert to_fixed_point_price("100.50") == 10050000000
    assert from_fixed_point_price(10050000000) == 100.50

    # Decimal input
    dec_in = Decimal("123.45678901")
    assert to_fixed_point_price(dec_in) == 12345678901
    assert from_fixed_point_price(12345678901) == pytest.approx(123.45678901, abs=1e-8)

    # Rejection of invalid inputs
    for bad in (float("nan"), float("inf"), -float("inf"), "nan", "inf", None):
        with pytest.raises(ValueError):
            to_fixed_point_price(bad)

    with pytest.raises(TypeError):
        from_fixed_point_price("not_an_int")  # type: ignore

    # 2. Decimal price conversion
    dec = to_decimal_price("50000.25")
    assert isinstance(dec, Decimal)
    assert dec == Decimal("50000.25")

    for bad in (float("nan"), float("inf"), "not_a_number", None):
        with pytest.raises(ValueError):
            to_decimal_price(bad)


def test_depth_ladder_nan_inf_immunity():
    """Verify ConsolidatedDepthEngine rejects non-finite NaN/inf prices and order sizes."""
    from mdrap.depth import ConsolidatedDepthEngine
    from mdrap.models import RawEvent

    depth_engine = ConsolidatedDepthEngine()

    # RawEvent with NaN and Inf in bids and asks
    raw = RawEvent(
        source="KRAKEN",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [
                [float("inf"), 1.0],      # Poison bid price
                [100.0, float("nan")],    # Poison bid size
                [100.0, float("inf")],    # Poison bid size
                [99.0, 5.0],              # Valid bid
            ],
            "asks": [
                [float("nan"), 2.0],      # Poison ask price
                [-float("inf"), 2.0],     # Poison ask price
                [101.0, float("nan")],    # Poison ask size
                [102.0, 3.0],             # Valid ask
            ],
        },
        receive_timestamp=1000.001,
        raw_id="poison_depth_1",
    )

    ladder = depth_engine.observe(raw)
    assert ladder is not None

    # Only valid finite levels must be present
    assert len(ladder.bids) == 1
    assert ladder.bids[0].price == 99.0
    assert ladder.bids[0].size == 5.0

    assert len(ladder.asks) == 1
    assert ladder.asks[0].price == 102.0
    assert ladder.asks[0].size == 3.0

    # compute_vwap rejects non-finite or non-positive target sizes
    for bad_size in (float("nan"), float("inf"), 0.0, -5.0, "bad"):
        with pytest.raises(ValueError):
            ladder.compute_vwap("BUY", bad_size)


def test_ingestlog_segment_header_crc_and_tamper_detection(tmp_path):
    """Verify segment header CRC is verified and bit-flips in header are detected loudly."""
    from mdrap.ingestlog import IngestLog, IngestLogCorruptError, SEGMENT_HEADER_SIZE, SEGMENT_HEADER_FORMAT
    from mdrap.models import RawEvent

    wal_dir = tmp_path / "crc_wal"
    log = IngestLog(str(wal_dir))

    # Append an event to force segment file creation
    raw = RawEvent(source="BINANCE", payload={"instrument": "BTC/USD", "price": 50000.0, "quantity": 1.0})
    log.append(raw)
    log.flush()
    log.close()

    seg_files = list(wal_dir.glob("segment_*.log"))
    assert len(seg_files) == 1
    seg_path = seg_files[0]

    # 1. Read segment header and verify CRC is non-zero and valid
    with open(seg_path, "rb") as f:
        hdr_bytes = f.read(SEGMENT_HEADER_SIZE)
    magic, ver, seg_start, ts, res = struct.unpack(SEGMENT_HEADER_FORMAT, hdr_bytes)
    hdr_crc = struct.unpack(">I", res[:4])[0]
    assert hdr_crc != 0
    expected_crc = zlib.crc32(hdr_bytes[:26]) & 0xFFFFFFFF
    assert hdr_crc == expected_crc

    # Verify log report is clean
    report = IngestLog.verify(str(wal_dir))
    assert report["is_clean"] is True

    # 2. Tamper with a byte inside the header (timestamp / offset at byte 15)
    with open(seg_path, "r+b") as f:
        f.seek(15)
        byte_val = f.read(1)[0]
        f.seek(15)
        f.write(bytes([byte_val ^ 0xFF]))
        f.flush()

    # Reopening or verifying log must detect the header corruption loudly
    report_tampered = IngestLog.verify(str(wal_dir))
    assert report_tampered["is_clean"] is False
    assert any("Corrupt segment header CRC" in err for err in report_tampered["errors"])

    with pytest.raises(IngestLogCorruptError) as exc:
        IngestLog(str(wal_dir))
    assert "Corrupt segment header CRC" in str(exc.value)


def test_ingestlog_legacy_header_backward_compatibility(tmp_path):
    """Verify legacy segment headers with zeroed reserved bytes remain 100% compatible."""
    from mdrap.ingestlog import IngestLog, LOG_MAGIC, LOG_VERSION, SEGMENT_HEADER_FORMAT, FRAME_MAGIC, FRAME_HEADER_FORMAT
    from mdrap.models import RawEvent

    wal_dir = tmp_path / "legacy_wal"
    wal_dir.mkdir(parents=True, exist_ok=True)

    # Manually write a legacy segment file with b"\x00" * 6 reserved bytes
    seg_path = wal_dir / "segment_000000000000.log"
    legacy_hdr = struct.pack(
        SEGMENT_HEADER_FORMAT,
        LOG_MAGIC,
        LOG_VERSION,
        0,
        1700000000.0,
        b"\x00" * 6,
    )
    payload_bytes = json.dumps({"source": "TEST", "payload": {"instrument": "BTC/USD", "price": 100.0}}).encode("utf-8")
    payload_crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
    frame_hdr = struct.pack(
        FRAME_HEADER_FORMAT,
        FRAME_MAGIC,
        0,
        0,
        1700000000.0,
        len(payload_bytes),
        payload_crc,
    )
    with open(seg_path, "wb") as f:
        f.write(legacy_hdr)
        f.write(frame_hdr)
        f.write(payload_bytes)

    # Legacy header must open and iterate cleanly without error
    log = IngestLog(str(wal_dir))
    records = list(log.iter_from(0))
    assert len(records) == 1
    assert records[0][0] == 0
    assert records[0][1].source == "TEST"
    log.close()







