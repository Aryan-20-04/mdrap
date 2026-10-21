"""Tri-Implementation Differential Fuzzing Test Suite.

Differential fuzzing harness comparing:
1. GoldenOracle (unoptimized frozen reference oracle)
2. QualityEngine (production optimized pure-Python hot path)
3. FastQualityEngine (native C hot path kernel)

Adversarial inputs:
- NaN, +0.0, -0.0, denormals, +Inf, -Inf, negative/zero prices
- Sequence wraps, extreme jumps, out-of-order bursts, duplicate bursts
- Mixed trade and quote streams
- Live vs Replay deterministic parity verification
"""

import copy
import math
import os
import random
import sys

import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason, RawEvent
from mdrap.quality import QualityConfig, QualityEngine
from mdrap.fastpath import FastQualityEngine, HAS_FASTPATH
from mdrap.engine import Engine, EngineConfig
from mdrap.clock import FixedClock
from tests.golden_oracle import GoldenOracle, GoldenConfig


def create_fuzz_event(
    idx: int,
    source: str = "FEED_A",
    instrument: str = "BTC-USDT",
    event_type: EventType = EventType.TRADE,
    seq: int | None = None,
    price: float | None = 100.0,
    qty: float | None = 1.0,
    bid: float | None = None,
    ask: float | None = None,
    bid_sz: float | None = None,
    ask_sz: float | None = None,
    exch_ts: float = 1700000000.0,
    recv_ts: float = 1700000000.01,
) -> CanonicalEvent:
    """Construct a CanonicalEvent for fuzz testing."""
    return CanonicalEvent(
        event_id=f"fuzz_{source}_{idx}",
        instrument_id=instrument,
        event_type=event_type,
        exchange_timestamp=exch_ts,
        receive_timestamp=recv_ts,
        source=source,
        sequence_number=seq,
        price=price,
        quantity=qty,
        bid_price=bid,
        bid_size=bid_sz,
        ask_price=ask,
        ask_size=ask_sz,
        source_id=0 if source == "FEED_A" else 1,
        instrument_id_int=1,
    )


def generate_adversarial_stream(seed: int, count: int = 1000) -> list[CanonicalEvent]:
    """Generate stream containing adversarial floats, timestamps, sequences, and quotes."""
    rng = random.Random(seed)
    events: list[CanonicalEvent] = []
    base_price = 100.0
    current_seq = 1000
    base_ts = 1700000000.0

    denormal_val = 1e-315

    for i in range(count):
        # Time progression
        base_ts += rng.uniform(0.001, 0.010)
        recv_ts = base_ts + rng.uniform(0.001, 0.005)
        ev_type = EventType.TRADE if rng.random() > 0.3 else EventType.QUOTE

        # Choose sequence anomaly
        r_seq = rng.random()
        if r_seq < 0.05:
            # Duplicate
            seq = current_seq
        elif r_seq < 0.10:
            # Out-of-order
            seq = max(1, current_seq - rng.randint(1, 10))
        elif r_seq < 0.15:
            # Gap
            current_seq += rng.randint(2, 20)
            seq = current_seq
        elif r_seq < 0.18:
            # Sequence wrap / boundary
            seq = 2**31 - 1 if rng.random() < 0.5 else 0
        elif r_seq < 0.22:
            # Unsequenced
            seq = None
        else:
            current_seq += 1
            seq = current_seq

        # Price / Quote generation
        r_float = rng.random()
        price = None
        bid = None
        ask = None
        qty = 10.0
        bid_sz = 5.0
        ask_sz = 5.0

        if ev_type == EventType.TRADE:
            if r_float < 0.03:
                price = math.nan
            elif r_float < 0.05:
                price = math.inf
            elif r_float < 0.07:
                price = -math.inf
            elif r_float < 0.10:
                price = -50.0  # negative
            elif r_float < 0.12:
                price = 0.0  # zero
            elif r_float < 0.14:
                price = denormal_val
            elif r_float < 0.17:
                price = base_price * 3.0  # Spike anomaly
            elif r_float < 0.20:
                price = base_price * 0.2  # Crash anomaly
            else:
                price = base_price + rng.gauss(0.0, 0.05)
        else:
            # Quote
            mid = base_price + rng.gauss(0.0, 0.05)
            spread = 0.10
            if r_float < 0.05:
                # Crossed quote
                bid = mid + 0.50
                ask = mid - 0.50
            elif r_float < 0.08:
                bid = math.nan
                ask = mid + spread
            elif r_float < 0.11:
                bid = -10.0
                ask = mid + spread
            else:
                bid = mid - spread / 2.0
                ask = mid + spread / 2.0

        # Timestamp skew / stale
        r_ts = rng.random()
        if r_ts < 0.03:
            # Implausibly future exchange_ts
            exch_ts = recv_ts + 5.0
        elif r_ts < 0.06:
            # Stale
            exch_ts = recv_ts - 0.20
        else:
            exch_ts = base_ts

        ev = create_fuzz_event(
            idx=i,
            source="FEED_A",
            instrument="BTC-USDT",
            event_type=ev_type,
            seq=seq,
            price=price,
            qty=qty,
            bid=bid,
            ask=ask,
            bid_sz=bid_sz,
            ask_sz=ask_sz,
            exch_ts=exch_ts,
            recv_ts=recv_ts,
        )
        events.append(ev)

    return events


@pytest.mark.parametrize("seed", [42, 101, 777, 999])
def test_differential_fuzz_tri_engine(seed: int):
    """Verify decision equivalence across GoldenOracle, QualityEngine, and FastQualityEngine."""
    events = generate_adversarial_stream(seed=seed, count=500)

    cfg = QualityConfig(
        staleness_threshold_s=0.05,
        price_anomaly_stddev=6.0,
        price_window=50,
        price_min_samples=20,
    )
    golden_cfg = GoldenConfig(
        staleness_threshold_s=0.05,
        price_anomaly_stddev=6.0,
        price_window=50,
        price_min_samples=20,
    )

    oracle = GoldenOracle(golden_cfg)
    py_engine = QualityEngine(cfg)

    c_engine = FastQualityEngine(cfg) if HAS_FASTPATH else None

    for i, orig_ev in enumerate(events):
        # 1. Oracle
        ev_oracle = copy.deepcopy(orig_ev)
        oracle_status, oracle_reasons, oracle_mask = oracle.evaluate(ev_oracle)

        # 2. Optimized Python QualityEngine
        ev_py = copy.deepcopy(orig_ev)
        py_engine.evaluate(ev_py)

        # Compare Oracle vs Python QualityEngine
        # Core decisions (QualityStatus) must agree strictly
        assert ev_py.quality_status == oracle_status, (
            f"[ORACLE vs PYTHON MISMATCH] at idx={i}, seed={seed}\n"
            f"Event: {orig_ev}\n"
            f"Oracle: status={oracle_status.value}, reasons={oracle_reasons}\n"
            f"Python: status={ev_py.quality_status.value}, reasons={ev_py.reasons}"
        )

        # Compare reason codes
        # Allow differences only in custom/derived reasons if any, but core invalid/suspicious reasons must match
        oracle_core_reasons = set(oracle_reasons)
        py_core_reasons = set(ev_py.reasons)
        # Check critical failure parity
        for crit in (
            Reason.SCHEMA_VIOLATION.value,
            Reason.CROSSED_QUOTE.value,
            Reason.TS_IMPLAUSIBLE.value,
        ):
            assert (crit in oracle_core_reasons) == (crit in py_core_reasons), (
                f"Mismatch on {crit} at idx={i}: Oracle={oracle_reasons}, Python={ev_py.reasons}"
            )

        # 3. Native C Hot Path (if available)
        if c_engine is not None:
            ev_c = copy.deepcopy(orig_ev)
            c_engine.evaluate(ev_c)

            assert ev_c.quality_status == ev_py.quality_status, (
                f"[PYTHON vs NATIVE C MISMATCH] at idx={i}, seed={seed}\n"
                f"Event: {orig_ev}\n"
                f"Python: status={ev_py.quality_status.value}, reasons={ev_py.reasons}\n"
                f"Native C: status={ev_c.quality_status.value}, reasons={ev_c.reasons}"
            )


def test_property_live_equals_replay(tmp_path):
    """Property test: Replaying events from WAL produces bit-identical decisions as live submission."""
    wal_dir = tmp_path / "wal"
    wal_dir.mkdir()

    clock = FixedClock(1700000000.0)
    cfg = EngineConfig(wal_dir=str(wal_dir), fsync_policy="always")

    # Generate stream of mixed events
    rng = random.Random(42)
    raw_events: list[RawEvent] = []

    for i in range(100):
        clock.advance(0.01)
        r_type = "TRADE" if i % 2 == 0 else "QUOTE"
        if r_type == "TRADE":
            payload = {
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": round(150.0 + rng.uniform(-1.0, 1.0), 2),
                "quantity": 100,
                "sequence": i + 1,
                "exchange_ts": clock.now(),
            }
        else:
            payload = {
                "instrument": "AAPL",
                "event_type": "QUOTE",
                "bid": 149.90,
                "ask": 150.10,
                "bid_size": 200,
                "ask_size": 200,
                "sequence": i + 1,
                "exchange_ts": clock.now(),
            }

        raw = RawEvent(source="XNAS", payload=payload, receive_timestamp=clock.now())
        raw_events.append(raw)

    # 1. Live processing
    live_engine = Engine(cfg, clock=clock)
    live_decisions = live_engine.submit(raw_events)
    live_engine.close()

    assert len(live_decisions) == 100

    # 2. Replay processing
    replay_engine = Engine(cfg, clock=clock)
    replay_decisions = replay_engine.replay()
    replay_engine.close()

    assert len(replay_decisions) == len(live_decisions)

    for i, (live_dec, rep_dec) in enumerate(zip(live_decisions, replay_decisions)):
        assert live_dec.offset == rep_dec.offset, f"Offset mismatch at {i}"
        assert live_dec.event_id == rep_dec.event_id, (
            f"Event ID mismatch at {i}: {live_dec.event_id} vs {rep_dec.event_id}"
        )
        assert live_dec.quality_status == rep_dec.quality_status, (
            f"QualityStatus mismatch at {i}"
        )
        assert set(live_dec.reasons) == set(rep_dec.reasons), f"Reasons mismatch at {i}"
