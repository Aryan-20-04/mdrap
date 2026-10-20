"""Test FastQualityEngine capacity clamping and override safety (Finding N-15).

Verifies that configuring MDRAP_FASTPATH_MAX_INSTRUMENTS above the compiled
native C table bounds (8,192 instruments, 32 sources) safely clamps effective
native thresholds and routes overflow events to the pure-Python fallback,
preventing false INVALID / SCHEMA_VIOLATION verdicts.
"""

from __future__ import annotations

import copy
import os
import random
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.quality import QualityConfig, QualityEngine
import mdrap.fastpath as fp
from mdrap.fastpath import FastQualityEngine


def _generate_events(
    n_instr: int, n_src: int, n: int = 5000, seed: int = 42
) -> list[CanonicalEvent]:
    r = random.Random(seed)
    t = 1.8e9
    seq: dict[tuple[str, str], int] = {}
    out: list[CanonicalEvent] = []
    srcs = [f"SRC_{j:02d}" for j in range(n_src)]

    for k in range(n):
        inst = f"INST_{r.randrange(n_instr):06d}"
        s = r.choice(srcs)
        key = (s, inst)
        seq[key] = seq.get(key, 0) + 1
        t += 0.001
        sn = seq[key] if r.random() > 0.05 else max(1, seq[key] - 1)
        out.append(
            CanonicalEvent(
                event_id=f"e_{k}",
                instrument_id=inst,
                event_type=EventType.TRADE,
                exchange_timestamp=t,
                receive_timestamp=t + 0.001,
                source=s,
                sequence_number=sn,
                price=100.0 + r.random(),
                quantity=1.0,
            )
        )
    return out


def test_compiled_capacity_accessors():
    """Verify compiled C capacity constants are correctly exported and clamped."""
    engine = FastQualityEngine()
    stats = engine.stats()

    assert stats["compiled_max_sources"] == 32
    assert stats["compiled_max_instruments"] == 8192
    assert stats["effective_max_sources"] <= 32
    assert stats["effective_max_instruments"] <= 8192


def test_override_above_compiled_capacity_routes_to_fallback_without_false_invalids(
    monkeypatch,
):
    """Auditor probe N-15 regression test: setting override above 8,192 must not cause false INVALIDs."""
    monkeypatch.setenv("MDRAP_FASTPATH_MAX_INSTRUMENTS", "65536")
    monkeypatch.setenv("MDRAP_FASTPATH_MAX_SOURCES", "64")

    cfg = QualityConfig()
    engine = FastQualityEngine(cfg)

    # Effective limit must remain clamped to physical compiled size
    assert engine.effective_max_instruments == 8192
    assert engine.effective_max_sources == 32
    assert engine.max_instruments == 65536

    # Generate events spanning 10,000 unique instruments (exceeding 8,192 compiled capacity)
    # Ensure all 10,000 instruments are generated in order so i_id reaches >= 8,192
    unique_inst_events = [
        CanonicalEvent(
            event_id=f"init_{k}",
            instrument_id=f"SYM_{k:05d}",
            event_type=EventType.TRADE,
            exchange_timestamp=1.8e9 + k * 0.001,
            receive_timestamp=1.8e9 + k * 0.001 + 0.0005,
            source=f"SRC_{k % 4:02d}",
            sequence_number=k + 1,
            price=100.0 + (k % 50) * 0.1,
            quantity=1.0,
        )
        for k in range(9000)
    ]
    random_events = _generate_events(n_instr=10000, n_src=4, n=1000, seed=123)
    events = unique_inst_events + random_events

    # Control run: Pure Python engine
    py_engine = QualityEngine(cfg)
    control_events = [copy.deepcopy(e) for e in events]
    for e in control_events:
        py_engine.evaluate(e)

    # Test single-event evaluation
    test_events = [copy.deepcopy(e) for e in events]
    for e in test_events:
        engine.evaluate(e)

    # Check 1: 0 decision divergences against Python oracle
    divergences = sum(
        1
        for a, b in zip(control_events, test_events)
        if (a.quality_status, tuple(sorted(a.reasons)))
        != (b.quality_status, tuple(sorted(b.reasons)))
    )
    assert divergences == 0, (
        f"Found {divergences} divergences between clamped native engine and Python engine!"
    )

    # Check 2: Fallback telemetry must record overflow events
    stats = engine.stats()
    assert stats["slot_overflow_count"] > 0
    assert stats["fallback_count"] == stats["slot_overflow_count"]

    # Check 3: Batch evaluation parity
    batch_engine = FastQualityEngine(cfg)
    batch_events = [copy.deepcopy(e) for e in events]
    for i in range(0, len(batch_events), 250):
        batch_engine.evaluate_batch(batch_events[i : i + 250])

    batch_divergences = sum(
        1
        for a, b in zip(control_events, batch_events)
        if (a.quality_status, tuple(sorted(a.reasons)))
        != (b.quality_status, tuple(sorted(b.reasons)))
    )
    assert batch_divergences == 0, f"Found {batch_divergences} batch divergences!"
