import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from fastpath import FastQualityEngine, NativeReplayBuffer
from gateway import normalize
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from protocol import HEADER_STRUCT, TICK_FRAME_LEN, unpack_tick_payload
from quality import QualityEngine
from simulator import FeedSimulator, SimulatorConfig


def _make_event(**overrides):
    base = dict(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=0.0,
        source="FEEDX",
        sequence_number=1,
        price=100.0,
        quantity=10.0,
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
            ev_c = normalize(
                RawEvent(
                    source=raw.source,
                    payload=raw.payload,
                    receive_timestamp=raw.receive_timestamp,
                    raw_id=raw.raw_id,
                )
            )
            res_py = py_eng.evaluate(ev_py)
            res_c = c_eng.evaluate(ev_c)

            assert res_py.quality_status == res_c.quality_status, (
                f"Status mismatch: py={res_py.quality_status}, c={res_c.quality_status}"
            )
        except Exception:
            pass

    # Compare summary counts
    assert py_eng.counts["VALID"] == c_eng.counts["VALID"]
    assert py_eng.counts["SUSPICIOUS"] == c_eng.counts["SUSPICIOUS"]
    assert py_eng.counts["INVALID"] == c_eng.counts["INVALID"]


def test_native_replay_buffer_record_and_query():
    buf = NativeReplayBuffer(capacity=65536)
    buf.clear()
    assert len(buf) == 0
    st = buf.stats()
    assert st["total_recorded"] == 0

    buf.record(
        seq=1,
        symbol="AAPL",
        source="FEEDX",
        price=150.0,
        size=10.0,
        bid=149.9,
        ask=150.1,
    )
    buf.record(
        seq=2,
        symbol="MSFT",
        source="FEEDY",
        price=320.0,
        size=5.0,
        bid=319.9,
        ask=320.1,
    )
    buf.record(
        seq=3,
        symbol="AAPL",
        source="FEEDX",
        price=150.5,
        size=20.0,
        bid=150.4,
        ask=150.6,
    )

    assert len(buf) == 3
    st = buf.stats()
    assert st["min_seq"] == 1
    assert st["max_seq"] == 3
    assert st["total_recorded"] == 3

    # Query full range
    events = buf.replay(1, 3)
    assert len(events) == 3
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[0]["sym"] == "AAPL"
    assert events[1]["sym"] == "MSFT"

    # Query with symbol filter
    aapl_events = buf.replay(1, 3, symbol="AAPL")
    assert len(aapl_events) == 2
    assert [e["seq"] for e in aapl_events] == [1, 3]

    msft_events = buf.replay(1, 3, symbol="MSFT")
    assert len(msft_events) == 1
    assert msft_events[0]["seq"] == 2
    assert msft_events[0]["sym"] == "MSFT"

    # Query empty range
    assert buf.replay(10, 20) == []
    assert buf.replay(3, 1) == []


def test_native_replay_buffer_binary_slice():
    buf = NativeReplayBuffer(capacity=65536)
    buf.clear()

    buf.record(
        seq=42,
        symbol="BTC-USDT",
        source="BINANCE",
        price=67500.5,
        size=2.5,
        bid=67500.0,
        ask=67501.0,
        status="VALID",
        is_crossed=False,
        exchange_ts=1700000000.0,
        ingest_ts=1700000000.001,
        broadcast_ts=1700000000.002,
        engine_us=1.8,
    )

    raw_bytes = buf.replay_binary(42, 42)
    assert len(raw_bytes) == TICK_FRAME_LEN  # 92 bytes

    magic, msg_type, payload_len = HEADER_STRUCT.unpack_from(raw_bytes, 0)
    assert magic == b"MD"
    assert msg_type == 1  # MSG_TYPE_TICK
    assert payload_len == 88

    payload = unpack_tick_payload(raw_bytes[4:])
    assert payload["seq"] == 42
    assert payload["sym"] == "BTC-USDT"
    assert payload["source"] == "BINANCE"
    assert payload["price"] == 67500.5
    assert payload["size"] == 2.5
    assert payload["bid"] == 67500.0
    assert payload["ask"] == 67501.0
    assert payload["status"] == "VALID"
    assert payload["is_crossed"] is False


def test_native_replay_buffer_ring_wraparound():
    buf = NativeReplayBuffer(capacity=65536)
    buf.clear()

    # Record 65,545 items (exceeds 65,536 slot capacity by 9 items)
    for s in range(1, 65546):
        buf.record(seq=s, symbol="AAPL", price=float(s), size=1.0)

    st = buf.stats()
    assert st["total_recorded"] == 65545
    assert st["capacity"] == 65536
    assert st["min_seq"] == 65545 - 65536 + 1  # seq 10
    assert st["max_seq"] == 65545
    assert len(buf) == 65536

    # Expired sequences should return empty
    assert buf.replay(1, 9) == []

    # Valid recent sequences should return exact slice
    active = buf.replay(65540, 65545)
    assert len(active) == 6
    assert active[0]["seq"] == 65540
    assert active[-1]["seq"] == 65545


def test_native_replay_buffer_fallback_parity():
    buf = NativeReplayBuffer(capacity=500)
    buf.is_native = False
    buf._fallback_deque = collections.deque(maxlen=500)

    buf.record(
        seq=10,
        symbol="ETH-USDT",
        source="KRAKEN",
        price=3500.25,
        size=10.0,
        bid=3500.0,
        ask=3500.5,
        status="VALID",
        is_crossed=False,
    )

    assert len(buf) == 1
    st = buf.stats()
    assert st["is_native"] is False
    assert st["min_seq"] == 10
    assert st["max_seq"] == 10
    assert st["total_recorded"] == 1

    events = buf.replay(10, 10)
    assert len(events) == 1
    assert events[0]["seq"] == 10
    assert events[0]["sym"] == "ETH-USDT"
    assert events[0]["price"] == 3500.25

    bin_bytes = buf.replay_binary(10, 10)
    assert len(bin_bytes) == TICK_FRAME_LEN
    payload = unpack_tick_payload(bin_bytes[4:])
    assert payload["seq"] == 10
    assert payload["sym"] == "ETH-USDT"
    assert payload["price"] == 3500.25


def test_multithreaded_fastpath_safety():
    import threading

    eng = FastQualityEngine()
    errors = []

    def worker(worker_id):
        for i in range(100):
            ev = _make_event(
                source=f"FEED_{worker_id}",
                sequence_number=i + 1,
                price=100.0 + (i % 10),
            )
            try:
                res = eng.evaluate(ev)
                if res.quality_status not in (
                    QualityStatus.VALID,
                    QualityStatus.SUSPICIOUS,
                    QualityStatus.INVALID,
                ):
                    errors.append(f"Unexpected status: {res.quality_status}")
            except Exception as e:
                errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Encountered concurrency errors in FastQualityEngine: {errors}"
