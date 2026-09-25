"""
Test Decision Identity Diff Harness (Invariant A4 & A5).

Verifies that the micro-batched evaluation path produces 100% identical decisions
(quality_status, reasons, reconciliation choices) compared to single-event evaluation
and pure Python baseline, while strictly preserving arrival sequence per source/instrument.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import QualityConfig
from fastpath import FastQualityEngine
from models import RawEvent
from pipeline import Pipeline
from quality import QualityEngine
from storage import Store


def _generate_synthetic_stream(count: int = 200) -> list[RawEvent]:
    """
    Generate a diverse, deterministic sequence of market events covering:
    - Normal trades and quotes
    - Sequence gaps
    - Crossed books
    - Staleness
    - Out of order exchange timestamps
    - Schema violations
    """
    events = []
    base_ts = 1700000000.0

    for i in range(count):
        raw_id = f"raw-{i:04d}"
        seq = i

        if i % 15 == 0:
            # Sequence gap
            seq = i + 10
        elif i % 25 == 0:
            # Out of order timestamp
            base_ts -= 5.0
        else:
            base_ts += 0.01

        recv_ts = base_ts + 0.005
        if i % 30 == 0:
            # Stale
            recv_ts = base_ts + 10.0

        if i % 40 == 0:
            # Crossed quote
            payload = {
                "type": "QUOTE",
                "symbol": "AAPL",
                "bid": 155.0,
                "ask": 150.0,
                "bid_size": 100,
                "ask_size": 200,
                "seq": seq,
                "timestamp": base_ts,
            }
        elif i % 50 == 0:
            # Schema violation / negative price
            payload = {
                "type": "TRADE",
                "symbol": "AAPL",
                "price": -10.0,
                "qty": 50,
                "seq": seq,
                "timestamp": base_ts,
            }
        else:
            # Normal trade
            payload = {
                "type": "TRADE",
                "symbol": "AAPL" if i % 2 == 0 else "MSFT",
                "price": 150.0 + (i % 10) * 0.5,
                "qty": 100.0,
                "seq": seq,
                "timestamp": base_ts,
            }

        events.append(
            RawEvent(
                source="NASDAQ" if i % 3 == 0 else "NYSE",
                payload=payload,
                receive_timestamp=recv_ts,
                raw_id=raw_id,
            )
        )

    return events


def test_decision_identity_engine_single_vs_batch():
    """
    Compare FastQualityEngine single vs evaluate_batch directly on normalized CanonicalEvents.
    """
    from gateway import ingest, normalize

    raw_stream = _generate_synthetic_stream(300)
    canonical_single = []
    canonical_batch = []

    for r in raw_stream:
        ingested = ingest(r)
        try:
            canonical_single.append(normalize(ingested))
            canonical_batch.append(normalize(ingested))
        except Exception:
            pass

    fast_single = FastQualityEngine(QualityConfig())
    fast_batch = FastQualityEngine(QualityConfig())

    # Single-event evaluation
    for ev in canonical_single:
        fast_single.evaluate(ev)

    # Batch evaluation
    fast_batch.evaluate_batch(canonical_batch)

    assert len(canonical_single) == len(canonical_batch)
    for i, (s_ev, b_ev) in enumerate(zip(canonical_single, canonical_batch)):
        assert s_ev.quality_status == b_ev.quality_status, (
            f"Status mismatch at idx {i}: {s_ev.quality_status} vs {b_ev.quality_status}"
        )
        assert sorted(s_ev.reasons) == sorted(b_ev.reasons), (
            f"Reasons mismatch at idx {i}: {s_ev.reasons} vs {b_ev.reasons}"
        )
        assert s_ev.instrument_id == b_ev.instrument_id
        assert s_ev.source == b_ev.source

    # Verify counts match
    assert fast_single.counts == fast_batch.counts
    assert fast_single.reason_counts == fast_batch.reason_counts


def test_decision_identity_pipeline_process_one_vs_process_batch():
    """
    Compare end-to-end Pipeline.process_one vs Pipeline.process_batch (Invariant A4 & A5).
    """
    raw_stream1 = _generate_synthetic_stream(250)
    raw_stream2 = _generate_synthetic_stream(250)

    store1 = Store(":memory:")
    store2 = Store(":memory:")

    pipe1 = Pipeline(store=store1, quality=FastQualityEngine())
    pipe2 = Pipeline(store=store2, quality=FastQualityEngine())

    results_one = []
    for r in raw_stream1:
        res = pipe1.process_one(r)
        results_one.append(res)
    pipe1.finish()

    # Process in chunks of 32 (micro-batches)
    results_batch = []
    chunk_size = 32
    for i in range(0, len(raw_stream2), chunk_size):
        chunk = raw_stream2[i : i + chunk_size]
        res = pipe2.process_batch(chunk)
        results_batch.extend(res)
    pipe2.finish()

    assert len(results_one) == len(results_batch)

    for i, (ev1, ev2) in enumerate(zip(results_one, results_batch)):
        if ev1 is None or ev2 is None:
            assert ev1 == ev2
            continue

        assert ev1.event_id == ev2.event_id
        assert ev1.instrument_id == ev2.instrument_id
        assert ev1.source == ev2.source
        assert ev1.quality_status == ev2.quality_status, (
            f"Event {i} quality_status mismatch: {ev1.quality_status} vs {ev2.quality_status}"
        )
        assert sorted(ev1.reasons) == sorted(ev2.reasons), (
            f"Event {i} reasons mismatch: {ev1.reasons} vs {ev2.reasons}"
        )

    # Invariant A5: per-source / per-instrument arrival order check
    for source in ("NASDAQ", "NYSE"):
        src_one = [ev.event_id for ev in results_one if ev and ev.source == source]
        src_batch = [ev.event_id for ev in results_batch if ev and ev.source == source]
        assert src_one == src_batch, f"Order violated for source {source}"

    for inst in ("AAPL", "MSFT"):
        inst_one = [
            ev.event_id for ev in results_one if ev and ev.instrument_id == inst
        ]
        inst_batch = [
            ev.event_id for ev in results_batch if ev and ev.instrument_id == inst
        ]
        assert inst_one == inst_batch, f"Order violated for instrument {inst}"


def test_decision_identity_python_vs_c_fastpath():
    """
    Compare decisions between pure Python QualityEngine and FastQualityEngine (Invariant A4).
    """
    from gateway import ingest, normalize

    raw_stream = _generate_synthetic_stream(200)
    py_engine = QualityEngine(QualityConfig())
    fast_engine = FastQualityEngine(QualityConfig())

    events_py = []
    events_fast = []
    for r in raw_stream:
        ingested = ingest(r)
        try:
            events_py.append(normalize(ingested))
            events_fast.append(normalize(ingested))
        except Exception:
            pass

    for ev_py, ev_fast in zip(events_py, events_fast):
        py_engine.evaluate(ev_py)
        fast_engine.evaluate(ev_fast)

        assert ev_py.quality_status == ev_fast.quality_status
        assert set(ev_py.reasons) == set(ev_fast.reasons)
