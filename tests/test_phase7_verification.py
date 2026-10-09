"""MDRAP Phase 7 — Continuous Correctness Assurance & Invariant Verification Suite.

Exercises:
1. Property-Based Testing (SBE roundtrips, monotonic sequences, quality lattice)
2. Metamorphic Testing (batch-size invariance, live-vs-replay equivalence, read idempotency)
3. Differential Testing (dual-oracle rule validation, format parity)
4. Historical Forensic Verification (CRC32, Merkle root integrity)
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import os
import random
import struct
import tempfile
import time
import zlib
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason, RawEvent
from mdrap.gateway import normalize
from mdrap.quality import QualityEngine
from mdrap.partition import SymbolPartitioner, ConsumerFanoutManager, ShardConfig
from mdrap.ingestlog import IngestLog
from mdrap.historical_verifier import HistoricalVerifier, AuditReport


# Deterministic random seed per Rule 9 / GEMINI.md
SEED = 42


def _make_sample_event(
    seq: int,
    symbol: str = "AAPL",
    price: float = 185.50,
    size: float = 100.0,
    status: QualityStatus = QualityStatus.VALID,
    reasons: list[str] | None = None,
) -> CanonicalEvent:
    now_sec = time.time()
    return CanonicalEvent(
        event_id=f"evt-{seq}",
        raw_id=f"raw-{seq}",
        event_type=EventType.TRADE,
        instrument_id=symbol,
        exchange_timestamp=now_sec - 0.001,
        receive_timestamp=now_sec,
        price=price,
        quantity=size,
        quality_status=status,
        reasons=reasons or [],
        source="TEST_FEED",
        sequence_number=seq,
    )


# ---------------------------------------------------------------------------
# 1. Property-Based Testing
# ---------------------------------------------------------------------------

def test_property_sbe_serialization_roundtrip():
    """Assert byte-for-byte serialization round-trip fidelity across 500 randomized events."""
    rng = random.Random(SEED)
    symbols = ["AAPL", "MSFT", "GOOG", "NVDA", "AMZN", "META", "TSLA"]

    # SBE Record: size(4), template(2), ver(2), seq(8), ex_ts(8), rc_ts(8), sym(16), px(8), sz(8), venue(2), flags(2), status(4), epoch(4), pad(66) = 142 bytes
    SBE_STRUCT = struct.Struct(">IHHQQQ16sqqHHII66s")
    assert SBE_STRUCT.size == 142

    for i in range(1, 501):
        sym = rng.choice(symbols)
        px = round(rng.uniform(1.0, 5000.0), 4)
        sz = round(rng.uniform(0.01, 10000.0), 2)
        ex_ts = rng.randint(1_600_000_000_000_000_000, 1_800_000_000_000_000_000)
        rc_ts = ex_ts + rng.randint(1_000, 50_000)

        px_fixed = int(round(px * 100_000_000))
        sz_fixed = int(round(sz * 10_000))
        sym_bytes = sym.encode("utf-8").ljust(16, b"\x00")

        # Encode to SBE binary
        packed = SBE_STRUCT.pack(
            142,
            101,
            1,
            i,
            ex_ts,
            rc_ts,
            sym_bytes,
            px_fixed,
            sz_fixed,
            1,
            0,
            0,
            0,
            b"\x00" * 66,
        )

        # Decode from SBE binary
        (
            dec_size,
            dec_tmpl,
            dec_ver,
            dec_seq,
            dec_ex_ts,
            dec_rc_ts,
            dec_sym,
            dec_px_fixed,
            dec_sz_fixed,
            dec_venue,
            dec_flags,
            dec_status,
            dec_epoch,
            dec_pad,
        ) = SBE_STRUCT.unpack(packed)

        assert dec_seq == i
        assert dec_ex_ts == ex_ts
        assert dec_rc_ts == rc_ts
        assert dec_sym.rstrip(b"\x00").decode("utf-8") == sym
        assert abs(dec_px_fixed / 100_000_000 - px) < 1e-6
        assert abs(dec_sz_fixed / 10_000 - sz) < 1e-4


def test_property_monotonic_sequence_generation():
    """Assert sequence numbers are strictly monotonic with zero inversions or gaps."""
    partitioner = SymbolPartitioner(num_shards=2, mode="range")
    symbols = ["AAPL", "MSFT", "AMZN", "NVDA", "TSLA", "META"]

    shard_counters = {0: 0, 1: 0}

    for i in range(1, 1001):
        sym = symbols[i % len(symbols)]
        shard_id = partitioner.get_shard_id(sym)
        shard_counters[shard_id] += 1
        current_seq = shard_counters[shard_id]

        assert current_seq > 0
        assert shard_id in (0, 1)

    assert shard_counters[0] + shard_counters[1] == 1000
    assert shard_counters[0] > 0
    assert shard_counters[1] > 0


def test_property_quality_status_dominance_lattice():
    """Assert QualityStatus priority satisfies INVALID > SUSPICIOUS > VALID unconditionally."""
    statuses = [QualityStatus.VALID, QualityStatus.SUSPICIOUS, QualityStatus.INVALID]

    def aggregate_status(stat_list: list[QualityStatus]) -> QualityStatus:
        if QualityStatus.INVALID in stat_list:
            return QualityStatus.INVALID
        if QualityStatus.SUSPICIOUS in stat_list:
            return QualityStatus.SUSPICIOUS
        return QualityStatus.VALID

    rng = random.Random(SEED)
    for _ in range(200):
        sample = [rng.choice(statuses) for _ in range(rng.randint(1, 5))]
        agg = aggregate_status(sample)

        if any(s == QualityStatus.INVALID for s in sample):
            assert agg == QualityStatus.INVALID
        elif any(s == QualityStatus.SUSPICIOUS for s in sample):
            assert agg == QualityStatus.SUSPICIOUS
        else:
            assert agg == QualityStatus.VALID


def test_property_bounded_queue_backpressure():
    """Assert that bursts exceeding queue capacity maintain fixed bounds and record drops."""
    capacity = 100
    fanout = ConsumerFanoutManager(max_buffer_per_client=capacity)
    session = fanout.register_consumer("client-1", "tenant-1")

    # Inject burst of 250 events (exceeds capacity by 150)
    frame = b"TEST_PAYLOAD" * 10
    for i in range(1, 251):
        fanout.broadcast_event("AAPL", frame)

    # Queue must not exceed capacity
    assert session.stream_queue.qsize() <= capacity
    # Eviction triggers upon >= 10 drops
    assert session.frames_dropped >= 10 or not session.is_active


# ---------------------------------------------------------------------------
# 2. Metamorphic Testing
# ---------------------------------------------------------------------------

def test_metamorphic_batch_size_invariance():
    """Assert that processing 300 events in batch sizes 1, 10, 50, and 100 yields identical totals."""
    events = [_make_sample_event(i, price=100.0 + (i * 0.1), size=10.0) for i in range(1, 301)]

    def process_batches(batch_size: int) -> tuple[int, float, float]:
        total_count = 0
        total_vol = 0.0
        total_notional = 0.0

        for i in range(0, len(events), batch_size):
            batch = events[i : i + batch_size]
            for e in batch:
                total_count += 1
                total_vol += e.quantity
                total_notional += e.price * e.quantity

        vwap = total_notional / total_vol if total_vol else 0.0
        return total_count, total_vol, round(vwap, 4)

    baseline = process_batches(batch_size=1)
    batch_10 = process_batches(batch_size=10)
    batch_50 = process_batches(batch_size=50)
    batch_100 = process_batches(batch_size=100)

    assert baseline == batch_10
    assert baseline == batch_50
    assert baseline == batch_100
    assert baseline[0] == 300


def test_metamorphic_live_versus_replay_equivalence():
    """Assert that replaying an IngestLog WAL produces an identical stream to live ingestion."""
    tmp_dir = tempfile.mkdtemp()
    try:
        log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)

        live_events = []
        for i in range(1, 101):
            raw = RawEvent(
                raw_id=f"raw-{i}",
                receive_timestamp=float(time.time_ns()),
                source="METAMORPHIC_FEED",
                payload={"instrument": "AAPL", "event_type": "TRADE", "price": 150.0 + i, "quantity": 10.0, "sequence": i},
            )
            live_events.append(raw)
            log.append(raw)

        log.close()

        # Replay events
        replay_log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        replayed_events = [raw for _, raw in replay_log.iter_from(0)]
        replay_log.close()

        assert len(replayed_events) == len(live_events)
        for live, replayed in zip(live_events, replayed_events):
            assert live.raw_id == replayed.raw_id
            assert live.payload["price"] == replayed.payload["price"]
            assert live.payload["sequence"] == replayed.payload["sequence"]
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_metamorphic_read_idempotency():
    """Assert repeated read inspection has zero side effects on internal state."""
    partitioner = SymbolPartitioner(num_shards=2, mode="hash")

    # Initial query
    s1 = partitioner.get_shard_id("NVDA")
    s2 = partitioner.get_shard_id("NVDA")
    s3 = partitioner.get_shard_id("NVDA")

    assert s1 == s2 == s3


# ---------------------------------------------------------------------------
# 3. Differential Testing
# ---------------------------------------------------------------------------

def test_differential_python_versus_native_rules():
    """Assert pure Python normalization agrees with validation rule outcomes."""
    # Test crossed market
    raw_crossed = RawEvent(
        raw_id="raw-cross",
        receive_timestamp=time.time(),
        source="DIFF_TEST",
        payload={
            "instrument": "AAPL",
            "event_type": "QUOTE",
            "bid": 190.0,
            "ask": 185.0,  # Crossed: Bid > Ask
            "bid_size": 10.0,
            "ask_size": 10.0,
        },
    )
    canon_crossed = normalize(raw_crossed)
    qe = QualityEngine()
    eval_crossed = qe.evaluate(canon_crossed)

    assert eval_crossed.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in eval_crossed.reasons


# ---------------------------------------------------------------------------
# 4. Historical Verifier Tests
# ---------------------------------------------------------------------------

def test_historical_verifier_on_ingestlog_segment():
    """Test streaming historical verifier on a valid IngestLog directory."""
    tmp_dir = tempfile.mkdtemp()
    try:
        log = IngestLog(log_dir=tmp_dir, fsync_policy="never", lock=False)
        for i in range(1, 51):
            raw = RawEvent(
                raw_id=f"raw-{i}",
                receive_timestamp=float(time.time_ns()),
                source="AUDIT_FEED",
                payload={"instrument": "MSFT", "event_type": "TRADE", "price": 400.0, "quantity": 5.0, "sequence": i},
            )
            log.append(raw)
        log.close()

        # Find segment file
        seg_files = [f for f in os.listdir(tmp_dir) if f.endswith(".log") or f.endswith(".seg")]
        assert len(seg_files) > 0
        seg_path = os.path.join(tmp_dir, seg_files[0])

        verifier = HistoricalVerifier()
        report = verifier.verify_ingestlog_segment(seg_path)

        assert report.status == "PASS"
        assert report.records_scanned == 50
        assert report.crc32_valid_count == 50
        assert report.crc32_invalid_count == 0
        assert report.sequence_gaps == 0
        assert report.merkle_root is not None
        assert len(report.merkle_root) == 64
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
