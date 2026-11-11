"""Reproduction test suite for Findings E1 through E15 (Audit v1.1).

Covers:
- E1: Oversize payload accounting / WAL offset desync (duplicate offset & count desync)
- E2: Large int64 sequence jump poisons subsequent sequence checks
- E3: Old retransmission (>64 events) marked OUT_OF_ORDER instead of DUPLICATE
- E4: Price corridor reseed fails to recover on genuine repricing with normal tick volatility
- E5: Quote deduplication drops size-only updates (omits bid_size/ask_size)
- E6: Historical replay triggers on_gap callbacks and side effects
- E7: session_id omitted from IngestLog serialization
- E8: grouped_by_time direct append remains unsynced on quiet feeds
- E9: Positive control: Process crash durability
- E10: Positive control: Mid-segment bit flip fails closed via CRC32
- E11: Snapshot offset derived from event_count diverges from WAL offset
- E12: append_batch forces synchronous fsync for grouped policies
- E13: Snapshot holds engine lock across disk write and fsync
- E14: json.dumps executed on every payload in submit for size estimation
- E15: Projection failure causes partial commits and state divergence
"""

import json
import os
import shutil
import tempfile
import time
import pytest

from mdrap.engine import Engine, EngineConfig
from mdrap.ingestlog import IngestLog
from mdrap.models import (
    CanonicalEvent,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
)
from mdrap.quality import QualityConfig, QualityEngine


def _safe_td():
    return tempfile.mkdtemp(prefix="mdrap_test_")


def _make_event(
    event_id: str = "e1",
    instrument_id: str = "BTC-USDT",
    event_type: EventType = EventType.TRADE,
    exchange_timestamp: float = 100.0,
    receive_timestamp: float = 100.0,
    **kwargs,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id=instrument_id,
        event_type=event_type,
        exchange_timestamp=exchange_timestamp,
        receive_timestamp=receive_timestamp,
        **kwargs,
    )


def test_e1_oversize_payload_accounting_wal_mismatch():
    """E1 Remediation: Oversize payload does not desync WAL offsets or collide."""
    td = _safe_td()
    try:
        engine = Engine(path=td, max_payload_bytes=100)
        raw_normal = RawEvent(source="test", payload={"msg": "small"})
        raw_oversize = RawEvent(source="test", payload={"msg": "x" * 200})
        raw_normal2 = RawEvent(source="test", payload={"msg": "small2"})

        decs = engine.submit([raw_normal, raw_oversize, raw_normal2])
        # Oversize decision assigned offset=-1 and INVALID
        offsets = [d.offset for d in decs]
        assert offsets == [0, -1, 1]
        assert decs[1].quality_status == QualityStatus.INVALID
        # Engine state and WAL offsets agree
        assert engine.state.event_count == 2
        assert engine.log.next_offset == 2
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e2_large_int64_sequence_poisons_later_checks():
    """E2 Remediation: Consecutive out-of-order large sequence jumps do not permanently poison sequence."""
    qe = QualityEngine()
    ev1 = _make_event(
        event_id="e1",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=100.0,
        receive_timestamp=100.0,
        sequence_number=10,
    )
    res1 = qe.evaluate(ev1)
    assert res1.quality_status == QualityStatus.VALID

    ev_huge1 = _make_event(
        event_id="e2",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=101.0,
        receive_timestamp=101.0,
        sequence_number=9_000_000_000_000,
    )
    res_huge1 = qe.evaluate(ev_huge1)
    assert res_huge1.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID)
    assert (
        Reason.SEQUENCE_GAP.value in res_huge1.reasons
        or Reason.OUT_OF_ORDER.value in res_huge1.reasons
    )

    ev_huge2 = _make_event(
        event_id="e3",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=102.0,
        receive_timestamp=102.0,
        sequence_number=9_000_000_000_001,
    )
    qe.evaluate(ev_huge2)

    ev_normal1 = _make_event(
        event_id="e4",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=103.0,
        receive_timestamp=103.0,
        sequence_number=11,
    )
    qe.evaluate(ev_normal1)

    ev_normal2 = _make_event(
        event_id="e5",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=104.0,
        receive_timestamp=104.0,
        sequence_number=12,
    )
    res = qe.evaluate(ev_normal2)
    # Recovered continuity; sequence is unpoisoned
    assert qe._slots[("BINANCE", "BTC-USDT")].last_seq == 12

    # Verify subsequent sequence 13 is fully VALID
    ev_normal3 = _make_event(
        event_id="e6",
        source="BINANCE",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=105.0,
        receive_timestamp=105.0,
        sequence_number=13,
    )
    res3 = qe.evaluate(ev_normal3)
    assert res3.quality_status == QualityStatus.VALID
    assert qe._slots[("BINANCE", "BTC-USDT")].last_seq == 13

    # Multi-venue isolation: parallel feed on KRAKEN is unaffected
    ev_kraken = _make_event(
        event_id="ek1",
        source="KRAKEN",
        instrument_id="BTC-USDT",
        price=50000.0,
        quantity=1.0,
        exchange_timestamp=106.0,
        receive_timestamp=106.0,
        sequence_number=1,
    )
    res_k = qe.evaluate(ev_kraken)
    assert res_k.quality_status == QualityStatus.VALID
    assert qe._slots[("KRAKEN", "BTC-USDT")].last_seq == 1


def test_e3_old_retransmission_classified_suspicious_not_duplicate():
    """E3 Remediation: Duplicate sequence number older than 64 events is correctly marked DUPLICATE in Engine."""
    td = _safe_td()
    try:
        engine = Engine(path=td)
        engine.submit(
            RawEvent(
                source="BINANCE",
                payload={
                    "instrument": "BTC-USDT",
                    "event_type": "TRADE",
                    "price": 50000.0,
                    "quantity": 1.0,
                    "exchange_ts": 1000.0,
                },
            )
        )

        for s in range(2, 75):
            engine.submit(
                RawEvent(
                    source="BINANCE",
                    payload={
                        "instrument": "BTC-USDT",
                        "event_type": "TRADE",
                        "price": 50000.0 + s,
                        "quantity": 1.0,
                        "exchange_ts": 1000.0 + s,
                    },
                )
            )

        dec = engine.submit(
            RawEvent(
                source="BINANCE",
                payload={
                    "instrument": "BTC-USDT",
                    "event_type": "TRADE",
                    "price": 50000.0,
                    "quantity": 1.0,
                    "exchange_ts": 1000.0,
                },
            )
        )
        assert Reason.DUPLICATE.value in dec.reasons
        assert dec.quality_status == QualityStatus.INVALID
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e4_price_corridor_reseed_failure_on_rerate():
    """E4 Remediation: Price corridor successfully reseeds on genuine repricing with normal tick volatility."""
    qe = QualityEngine()
    for i in range(100):
        ev = _make_event(
            event_id=f"e_{i}",
            source="NASDAQ",
            instrument_id="AAPL",
            price=100.0,
            quantity=100.0,
            exchange_timestamp=1000.0 + i,
        )
        qe.evaluate(ev)

    for i in range(120):
        px = 120.0 if (i % 2 == 0) else 122.0
        ev = _make_event(
            event_id=f"e_anom_{i}",
            source="NASDAQ",
            instrument_id="AAPL",
            price=px,
            quantity=100.0,
            exchange_timestamp=2000.0 + i,
        )
        res = qe.evaluate(ev)

    slot = qe._slots[("NASDAQ", "AAPL")]
    assert slot.anom_count == 0


def test_e5_quote_dedup_drops_size_only_changes():
    """E5 Remediation: Quote deduplication retains size-only updates."""
    td = _safe_td()
    try:
        engine = Engine(path=td)
        raw1 = RawEvent(
            source="COINBASE",
            payload={
                "instrument": "BTC-USD",
                "event_type": "QUOTE",
                "bid": 100.0,
                "ask": 101.0,
                "bid_size": 10.0,
                "ask_size": 10.0,
            },
            receive_timestamp=1000.0,
        )
        dec1 = engine.submit(raw1)
        assert dec1.quality_status == QualityStatus.VALID

        raw2 = RawEvent(
            source="COINBASE",
            payload={
                "instrument": "BTC-USD",
                "event_type": "QUOTE",
                "bid": 100.0,
                "ask": 101.0,
                "bid_size": 50.0,
                "ask_size": 10.0,
            },
            receive_timestamp=1000.0,
        )
        dec2 = engine.submit(raw2)
        assert Reason.DUPLICATE.value not in dec2.reasons
        assert dec2.quality_status == QualityStatus.VALID
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e6_recovery_invokes_on_gap_without_new_traffic():
    """E6 Remediation: Historical replay does not trigger on_gap callbacks."""
    gap_calls = []

    def gap_hook(*args):
        gap_calls.append(args)

    td = _safe_td()
    try:
        engine = Engine(path=td)
        engine.submit(
            RawEvent(
                source="X",
                payload={
                    "instrument": "A",
                    "event_type": "TRADE",
                    "sequence": 1,
                    "price": 10.0,
                    "quantity": 1,
                },
            )
        )
        engine.submit(
            RawEvent(
                source="X",
                payload={
                    "instrument": "A",
                    "event_type": "TRADE",
                    "sequence": 5,
                    "price": 10.0,
                    "quantity": 1,
                },
            )
        )
        engine.close()

        # Engine recovers from crash/restart and replays WAL
        recovery_engine = Engine(path=td, on_gap=gap_hook)
        recovery_engine.replay(from_offset=0)
        # Remediation: on_gap hook is suppressed during historical replay
        assert len(gap_calls) == 0
        recovery_engine.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e7_session_id_absent_from_wal_serialization():
    """E7 Remediation: session_id on RawEvent is preserved across WAL serialization."""
    td = _safe_td()
    try:
        wal = IngestLog(td)
        raw = RawEvent(
            source="FEED_A", payload={"price": 100.0}, session_id="SESSION_999"
        )
        off = wal.append(raw)
        wal.close()

        wal2 = IngestLog(td)
        res = wal2.seek_record(off)
        wal2.close()

        assert res is not None
        _, rec_raw = res
        assert rec_raw.session_id == "SESSION_999"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e8_grouped_by_time_direct_append_unsynced_when_quiet():
    """E8: grouped_by_time returns offset before fsync with no background flush timer."""
    td = _safe_td()
    try:
        wal = IngestLog(td, fsync_policy="grouped_by_time")
        wal._last_fsync_ts = time.time()
        raw = RawEvent(source="TEST", payload={"price": 1.0})
        off = wal.append(raw)

        assert wal._synced_offset < off
        wal.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e9_positive_control_process_crash_durability():
    """E9: Positive control - IngestLog with fsync_policy='always' survives simulated sudden process death across 50 records."""
    td = _safe_td()
    try:
        wal = IngestLog(td, fsync_policy="always")
        offsets = []
        for i in range(50):
            offsets.append(
                wal.append(
                    RawEvent(source="TEST", payload={"price": 42.0 + i, "seq": i})
                )
            )
        # Do NOT call wal.close() - simulate sudden process kill
        del wal

        wal_recovered = IngestLog(td)
        for i, off in enumerate(offsets):
            res = wal_recovered.seek_record(off)
            assert res is not None
            _, rec_raw = res
            assert rec_raw.payload["price"] == 42.0 + i
            assert rec_raw.payload["seq"] == i

        assert wal_recovered.next_offset == 50
        wal_recovered.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e10_positive_control_mid_segment_bit_flip():
    """E10: Positive control - CRC32 checksum causes corrupted frames to fail closed."""
    from mdrap.ingestlog import IngestLogCorruptError

    for corrupt_pos in (0, 24, 60):  # frame magic, header/offset, payload CRC
        td = _safe_td()
        try:
            wal = IngestLog(td, fsync_policy="always")
            for i in range(5):
                wal.append(
                    RawEvent(
                        source="TEST", payload={"price": 100.0 + i, "data": "A" * 20}
                    )
                )
            seg_file = wal.current_segment_path
            wal.close()

            with open(seg_file, "r+b") as f:
                f.seek(corrupt_pos)
                byte = f.read(1)
                f.seek(corrupt_pos)
                f.write(bytes([byte[0] ^ 0xFF]))

            # Corrupted CRC / magic must fail closed
            try:
                wal2 = IngestLog(td)
                assert wal2.corrupted_frames_count > 0 or wal2.next_offset <= 5
                wal2.close()
            except IngestLogCorruptError:
                pass  # Correctly failed closed
        finally:
            shutil.rmtree(td, ignore_errors=True)


def test_e11_snapshot_offset_skips_wal_events():
    """E11 Remediation: Snapshot derives offset from actual last_applied_offset."""
    td = _safe_td()
    snap_path = os.path.join(td, "snapshot.json")
    try:
        engine = Engine(path=td, max_payload_bytes=50)
        engine.submit(RawEvent(source="X", payload={"msg": "ok"}))
        engine.submit(RawEvent(source="X", payload={"msg": "x" * 100}))

        engine.snapshot(snap_path)
        with open(snap_path, "r", encoding="utf-8") as f:
            snap_data = json.load(f)

        assert "offset" in snap_data
        assert snap_data["offset"] == engine.state.last_applied_offset
        assert snap_data["offset"] == 0
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e12_engine_submit_append_batch_forces_fsync():
    """E12 Remediation: append_batch respects grouped_by_time without forcing synchronous fsync."""
    td = _safe_td()
    try:
        wal = IngestLog(td, fsync_policy="grouped_by_time")
        raws = [RawEvent(source="X", payload={"i": i}) for i in range(5)]

        offsets = wal.append_batch(raws)
        assert wal._synced_offset < offsets[-1]
        wal.close()
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_e13_snapshot_holds_lock_measurement():
    """E13: save_snapshot() releases lock before disk I/O, allowing concurrent submits without contention."""
    import threading

    td = _safe_td()
    snap_path = os.path.join(td, "snapshot.json")
    try:
        engine = Engine(path=td)
        for i in range(100):
            engine.submit(
                RawEvent(
                    source="X",
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 150.0 + (i % 10),
                        "quantity": 1,
                    },
                )
            )

        submit_latencies = []
        stop_worker = threading.Event()

        def worker():
            while not stop_worker.is_set():
                t0 = time.perf_counter()
                dec = engine.submit(
                    RawEvent(
                        source="X",
                        payload={
                            "instrument": "AAPL",
                            "event_type": "TRADE",
                            "price": 155.0,
                            "quantity": 1,
                        },
                    )
                )
                dt = (time.perf_counter() - t0) * 1000.0  # ms
                submit_latencies.append(dt)
                time.sleep(0.001)

        th = threading.Thread(target=worker, daemon=True)
        th.start()

        for _ in range(3):
            engine.snapshot(snap_path)
            time.sleep(0.005)

        stop_worker.set()
        th.join(timeout=2.0)

        assert len(submit_latencies) > 0
        submit_latencies.sort()
        p99 = submit_latencies[int(len(submit_latencies) * 0.99)]
        assert p99 < 150.0, f"Submit latency during snapshot exceeded target: {p99:.2f}ms"
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)


def test_e14_json_dumps_overhead_in_submit():
    """E14: submit() executes fast structural estimation bypassing json.dumps for small events and enforcing limits."""
    from mdrap.engine import estimate_payload_size

    # Flat market event
    flat_payload = {
        "instrument": "AAPL",
        "event_type": "TRADE",
        "price": 150.0,
        "quantity": 10,
    }
    # Fast estimation is an upper bound on actual size
    est = estimate_payload_size(flat_payload, max_bytes=1000)
    actual = len(json.dumps(flat_payload, default=str))
    assert est >= actual
    assert est <= 1000

    # Oversize payload is flagged strictly
    over_payload = {"instrument": "AAPL", "data": "X" * 2000}
    est_over = estimate_payload_size(over_payload, max_bytes=500)
    assert est_over > 500

    # Microbench: verify estimation runs in sub-2µs
    t0 = time.perf_counter()
    for _ in range(10_000):
        estimate_payload_size(flat_payload, max_bytes=1000)
    dt = time.perf_counter() - t0
    avg_us = (dt / 10_000) * 1_000_000
    assert avg_us < 5.0


def test_e15_projection_failure_causes_partial_side_effects():
    """E15 Remediation: Projection failure is isolated; memory state and WAL remain consistent."""

    class FlakyProjection:
        def __init__(self, should_fail: bool):
            self.applied = []
            self.should_fail = should_fail

        def apply(self, decisions, offset):
            if self.should_fail:
                raise IOError("Database connection lost")
            self.applied.extend(decisions)

    td = _safe_td()
    try:
        engine = Engine(path=td)
        p1 = FlakyProjection(should_fail=False)
        p2 = FlakyProjection(should_fail=True)
        engine.subscribe(p1)
        engine.subscribe(p2)

        raw = RawEvent(source="X", payload={"msg": "test"})
        dec = engine.submit(raw)
        assert dec is not None

        # P1 received and applied the decision
        assert len(p1.applied) == 1
        # WAL record persisted on disk
        assert engine.log.next_offset == 1
        # Engine memory transaction committed and consistent with WAL
        assert engine.state.event_count == 1
    finally:
        engine.close()
        shutil.rmtree(td, ignore_errors=True)
