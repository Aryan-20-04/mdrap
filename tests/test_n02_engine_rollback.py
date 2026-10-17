"""Regression test for Audit Finding N-02: Exact EngineState rollback isolation on failed step.

Verifies:
1. EngineState.clone() creates completely independent 2-level dict copies of bbo_state.
2. In-place mutations to bbo_state in a cloned state or during a failed _step_internal
   never leak into the rolled-back engine state.
"""

from __future__ import annotations

import copy
import pytest

from mdrap.engine import Engine, EngineConfig, EngineState
from mdrap.models import RawEvent, QualityStatus


def test_n02_clone_bbo_state_independence() -> None:
    """Verify EngineState.clone() creates deep independent copies of nested bbo_state dicts."""
    state = EngineState()
    state.bbo_state["AAPL"] = {
        "NASDAQ": {"bid": 150.0, "ask": 150.10},
        "ARCA": {"bid": 149.95, "ask": 150.05},
    }

    cloned = state.clone()

    # Verify inner dicts are distinct objects, not shared aliases
    assert cloned.bbo_state["AAPL"]["NASDAQ"] is not state.bbo_state["AAPL"]["NASDAQ"]
    assert cloned.bbo_state["AAPL"]["ARCA"] is not state.bbo_state["AAPL"]["ARCA"]

    # Mutate the original inner dict
    state.bbo_state["AAPL"]["NASDAQ"]["bid"] = 999.99
    state.bbo_state["AAPL"]["NASDAQ"]["ask"] = 1000.00

    # Cloned state MUST preserve original values
    assert cloned.bbo_state["AAPL"]["NASDAQ"]["bid"] == 150.0
    assert cloned.bbo_state["AAPL"]["NASDAQ"]["ask"] == 150.10


def test_n02_step_failure_rollback_exactness() -> None:
    """Verify that an unhandled exception in _step_internal rolls back bbo_state exactly."""
    engine = Engine(EngineConfig(fsync_policy="never"))

    # Establish baseline quotes across two feeds
    engine.submit(
        RawEvent(
            source="FEED_A",
            payload={
                "instrument": "MSFT",
                "event_type": "QUOTE",
                "bid": 400.0,
                "ask": 400.5,
                "sequence": 1,
            },
            receive_timestamp=1000.0,
        )
    )
    engine.submit(
        RawEvent(
            source="FEED_B",
            payload={
                "instrument": "MSFT",
                "event_type": "QUOTE",
                "bid": 400.1,
                "ask": 400.6,
                "sequence": 1,
            },
            receive_timestamp=1000.1,
        )
    )

    baseline_bbo = copy.deepcopy(engine.state.bbo_state)
    assert baseline_bbo["MSFT"]["FEED_A"]["bid"] == 400.0
    assert baseline_bbo["MSFT"]["FEED_B"]["bid"] == 400.1

    # Inject failure into _step_internal *after* bbo_state is mutated
    orig_step_internal = engine._step_internal

    def failing_step_internal(state, raw_event, clock, offset=None):
        # Call original to mutate state (including bbo_state)
        st, dec = orig_step_internal(state, raw_event, clock, offset=offset)
        # Raise simulated unexpected error after state mutation
        raise RuntimeError("Simulated unhandled exception after bbo_state mutation")

    engine._step_internal = failing_step_internal  # type: ignore[assignment]

    # Submit event that mutates FEED_A quote
    bad_raw = RawEvent(
        source="FEED_A",
        payload={
            "instrument": "MSFT",
            "event_type": "QUOTE",
            "bid": 420.0,
            "ask": 420.5,
            "sequence": 2,
        },
        receive_timestamp=1001.0,
    )

    # step() catches exception and restores state_backup
    new_state, dec = engine.step(engine.state, bad_raw, engine.clock)
    engine.state = new_state

    # Decision is quarantined as INVALID
    assert dec.quality_status == QualityStatus.INVALID

    # Critical check: bbo_state MUST be rolled back to exact pre-step baseline
    # Under the old shallow clone, FEED_A bid leaked as 420.0
    assert engine.state.bbo_state["MSFT"]["FEED_A"]["bid"] == 400.0
    assert engine.state.bbo_state["MSFT"]["FEED_A"]["ask"] == 400.5
    assert engine.state.bbo_state["MSFT"]["FEED_B"]["bid"] == 400.1
    assert engine.state.bbo_state == baseline_bbo

    engine.close()
