"""
Test Backlog Reconciliation Invariance (Invariant Q4).

Verifies that Reconciler.reconcile() strictly evaluates agreement_window_s
against exchange_timestamp (simulated market time) rather than receive_timestamp
or wall-clock processing time, ensuring cross-venue consensus is 100% invariant
under artificial processing backlog, burst replays, or network queuing delays.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus
from reconciliation import Reconciler, ReliabilityConfig, ReliabilityTracker


def _create_canonical_trade(
    event_id: str,
    instrument: str,
    source: str,
    exchange_ts: float,
    receive_ts: float,
    price: float,
    qty: float = 100.0,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=exchange_ts,
        receive_timestamp=receive_ts,
        processing_timestamp=0.0,
        source=source,
        sequence_number=1,
        price=price,
        quantity=qty,
        quality_status=QualityStatus.VALID,
    )


def test_reconciliation_invariant_under_backlog():
    """
    Scenario A: Real-time processing (receive_timestamp ≈ exchange_timestamp).
    Scenario B: Backlog processing (receive_timestamp delayed by 500s).

    Invariant Q4 dictates both scenarios must produce identical CanonicalDecisions.
    """
    cfg = ReliabilityConfig(agreement_window_s=0.5, disagreement_pct_threshold=0.01)

    # Scenario A: Real-time
    rel_a = ReliabilityTracker(cfg)
    reconciler_a = Reconciler(reliability=rel_a, config=cfg)

    ev_nasdaq_a = _create_canonical_trade(
        event_id="evt-nasdaq-1",
        instrument="AAPL",
        source="NASDAQ",
        exchange_ts=1000.0,
        receive_ts=1000.001,
        price=150.00,
    )
    ev_nyse_a = _create_canonical_trade(
        event_id="evt-nyse-1",
        instrument="AAPL",
        source="NYSE",
        exchange_ts=1000.2,
        receive_ts=1000.201,
        price=150.05,
    )

    reconciler_a.reconcile(ev_nasdaq_a)
    dec_a = reconciler_a.reconcile(ev_nyse_a)
    assert dec_a is not None, "Quorum should be established in real-time"

    # Scenario B: High backlog / artificial delay (receive_timestamp delayed by 500 wall-clock seconds)
    rel_b = ReliabilityTracker(cfg)
    reconciler_b = Reconciler(reliability=rel_b, config=cfg)

    ev_nasdaq_b = _create_canonical_trade(
        event_id="evt-nasdaq-1",
        instrument="AAPL",
        source="NASDAQ",
        exchange_ts=1000.0,
        receive_ts=1000.001,
        price=150.00,
    )
    ev_nyse_b = _create_canonical_trade(
        event_id="evt-nyse-1",
        instrument="AAPL",
        source="NYSE",
        exchange_ts=1000.2,
        receive_ts=1500.200,  # 500 seconds backlog delay!
        price=150.05,
    )

    reconciler_b.reconcile(ev_nasdaq_b)
    dec_b = reconciler_b.reconcile(ev_nyse_b)
    assert dec_b is not None, (
        "Quorum MUST still be established under backlog because exchange_timestamp is within 0.5s"
    )

    # Decisions must be identical
    assert dec_a.chosen_source == dec_b.chosen_source
    assert dec_a.chosen_event_id == dec_b.chosen_event_id
    assert dec_a.disagreement == dec_b.disagreement
    assert dec_a.competing_sources == dec_b.competing_sources


def test_reconciliation_exceeding_agreement_window():
    """
    Verify that when exchange_timestamp difference genuinely exceeds agreement_window_s,
    quorum is NOT formed even if receive_timestamp is identical.
    """
    cfg = ReliabilityConfig(agreement_window_s=0.5)
    reconciler = Reconciler(config=cfg)

    ev_nasdaq = _create_canonical_trade(
        event_id="evt-nasdaq-1",
        instrument="AAPL",
        source="NASDAQ",
        exchange_ts=1000.0,
        receive_ts=2000.0,
        price=150.00,
    )
    ev_nyse = _create_canonical_trade(
        event_id="evt-nyse-1",
        instrument="AAPL",
        source="NYSE",
        exchange_ts=1001.0,  # 1.0s apart in market time > 0.5s window
        receive_ts=2000.0,  # Arrived simultaneously in wall-clock time
        price=150.05,
    )

    reconciler.reconcile(ev_nasdaq)
    dec = reconciler.reconcile(ev_nyse)
    assert dec is None, "Quorum should NOT form when exchange_timestamp gap exceeds window"
