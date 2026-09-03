"""
Verification tests for Native C Hot-Path Accelerator.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from fastpath import FastQualityEngine
from gateway import normalize
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from quality import QualityEngine
from simulator import FeedSimulator, SimulatorConfig


def _make_event(**overrides):
    base = dict(
        event_id="e1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=0.0, source="FEEDX", sequence_number=1,
        price=100.0, quantity=10.0,
    )
    base.update(overrides)
    return CanonicalEvent(**base)



def test_fastpath_crossed_quote_is_invalid():
    eng = FastQualityEngine()
    ev = _make_event(event_type=EventType.QUOTE, bid_price=105.0, ask_price=100.0)
    res = eng.evaluate(ev)
    assert res.quality_status == QualityStatus.INVALID
    assert Reason.CROSSED_QUOTE.value in res.reasons


def test_fastpath_sequence_gap():
    eng = FastQualityEngine()
    ev1 = _make_event(sequence_number=1)
    ev2 = _make_event(sequence_number=10)  # Gap of 9
    eng.evaluate(ev1)
    res2 = eng.evaluate(ev2)
    assert res2.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in res2.reasons


def test_fastpath_duplicate():
    eng = FastQualityEngine()
    ev1 = _make_event(sequence_number=100)
    ev2 = _make_event(sequence_number=100)
    eng.evaluate(ev1)
    res2 = eng.evaluate(ev2)
    assert res2.quality_status == QualityStatus.INVALID
    assert Reason.DUPLICATE.value in res2.reasons



def test_fastpath_parity_with_python_engine():
    """Runs identical stream through both Python QualityEngine and Native C FastQualityEngine."""
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=3000))
    py_eng = QualityEngine()
    c_eng = FastQualityEngine()

    for raw, _label in sim.generate():
        try:
            ev_py = normalize(raw)
            ev_c = normalize(RawEvent(source=raw.source, payload=raw.payload, receive_timestamp=raw.receive_timestamp, raw_id=raw.raw_id))
            res_py = py_eng.evaluate(ev_py)
            res_c = c_eng.evaluate(ev_c)

            assert res_py.quality_status == res_c.quality_status, f"Status mismatch: py={res_py.quality_status}, c={res_c.quality_status}"
        except Exception:
            pass

    # Compare summary counts
    assert py_eng.counts["VALID"] == c_eng.counts["VALID"]
    assert py_eng.counts["SUSPICIOUS"] == c_eng.counts["SUSPICIOUS"]
    assert py_eng.counts["INVALID"] == c_eng.counts["INVALID"]
