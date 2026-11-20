"""Regression and correctness tests for independent quote reconciliation (MDRAP-FIN-02).

Verifies:
- Separation of trade and quote reconciliation state per instrument.
- Independent bid and ask disagreement detection across multiple venues.
- Crossed book detection (individual and cross-venue) tagging Reason.CROSSED_QUOTE.
- Canonical quote tie-breaking: Score -> Tighter Spread -> Larger Depth -> Source Name.
- Median quote selection by midpoint.
- Strict quorum and temporal window enforcement for quote streams.
"""

import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.reconciliation import (
    CanonicalDecision,
    HighestReliabilityStrategy,
    MedianPriceStrategy,
    ReconciliationConfig,
    Reconciler,
    ReliabilityTracker,
)


def _make_quote(
    source: str,
    bid_price: float,
    ask_price: float,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    ts: float = 1000.0,
    instrument: str = "BTC-USDT",
    event_id: str = "",
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id or f"quote_{source}_{bid_price}_{ask_price}",
        instrument_id=instrument,
        event_type=EventType.QUOTE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=source,
        price=None,
        quantity=None,
        bid_price=bid_price,
        ask_price=ask_price,
        bid_size=bid_size,
        ask_size=ask_size,
        quality_status=QualityStatus.VALID,
    )


def _make_trade(
    source: str,
    price: float,
    quantity: float = 1.0,
    ts: float = 1000.0,
    instrument: str = "BTC-USDT",
    event_id: str = "",
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id or f"trade_{source}_{price}",
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=source,
        price=price,
        quantity=quantity,
        quality_status=QualityStatus.VALID,
    )


def test_quote_reconciliation_agreement():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"BINANCE": 0.95, "COINBASE": 0.90}
    reconciler = Reconciler(reliability=tracker)

    q1 = _make_quote("BINANCE", bid_price=50000.0, ask_price=50001.0)
    assert reconciler.reconcile(q1) is None  # Quorum not reached

    q2 = _make_quote("COINBASE", bid_price=50000.1, ask_price=50001.1)
    dec = reconciler.reconcile(q2)

    assert dec is not None
    assert dec.reconciliation_type == "QUOTE"
    assert dec.chosen_source == "BINANCE"  # Higher score
    assert dec.disagreement is False
    assert dec.bid_disagreement is False
    assert dec.ask_disagreement is False
    assert dec.is_crossed_book is False


def test_quote_reconciliation_bid_and_ask_disagreement():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"VENUE_A": 0.85, "VENUE_B": 0.90}
    cfg = ReconciliationConfig(disagreement_pct_threshold=0.01)  # 1%
    reconciler = Reconciler(reliability=tracker, cfg=cfg)

    # VENUE_A has bid=50000, ask=50010
    # VENUE_B has bid=52000 (> 3% disagreement), ask=52010
    q_a = _make_quote("VENUE_A", bid_price=50000.0, ask_price=50010.0)
    q_b = _make_quote("VENUE_B", bid_price=52000.0, ask_price=52010.0)

    reconciler.reconcile(q_a)
    dec = reconciler.reconcile(q_b)

    assert dec is not None
    assert dec.reconciliation_type == "QUOTE"
    assert dec.disagreement is True
    assert dec.bid_disagreement is True
    assert dec.ask_disagreement is True
    assert Reason.CROSS_FEED_DISAGREEMENT.value in dec.quality_reasons
    assert dec.chosen_source == "VENUE_B"


def test_quote_reconciliation_crossed_book_detection():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"VENUE_A": 0.95, "VENUE_B": 0.90}
    reconciler = Reconciler(reliability=tracker)

    # Cross-venue crossed book: VENUE_A bid (50005) > VENUE_B ask (50000)
    q_a = _make_quote("VENUE_A", bid_price=50005.0, ask_price=50010.0)
    q_b = _make_quote("VENUE_B", bid_price=49995.0, ask_price=50000.0)

    reconciler.reconcile(q_a)
    dec = reconciler.reconcile(q_b)

    assert dec is not None
    assert dec.reconciliation_type == "QUOTE"
    assert dec.is_crossed_book is True
    assert dec.disagreement is True
    assert Reason.CROSSED_QUOTE.value in dec.quality_reasons


def test_quote_reconciliation_individual_crossed_book():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"VENUE_A": 0.95, "VENUE_B": 0.90}
    reconciler = Reconciler(reliability=tracker)

    # Inverted quote on VENUE_A: bid (50010) >= ask (50000)
    q_a = _make_quote("VENUE_A", bid_price=50010.0, ask_price=50000.0)
    q_b = _make_quote("VENUE_B", bid_price=49995.0, ask_price=50005.0)

    reconciler.reconcile(q_a)
    dec = reconciler.reconcile(q_b)

    assert dec is not None
    assert dec.is_crossed_book is True
    assert Reason.CROSSED_QUOTE.value in dec.quality_reasons


def test_quote_reconciliation_tie_breaking_spread_and_depth():
    tracker = ReliabilityTracker()
    # Identical reliability score
    tracker._cached_scores = {"FEED_1": 0.90, "FEED_2": 0.90, "FEED_3": 0.90}
    reconciler = Reconciler(reliability=tracker)

    # FEED_1: spread 2.0 (50000 to 50002)
    # FEED_2: spread 1.0 (50000 to 50001) -> tighter spread wins!
    q1 = _make_quote("FEED_1", bid_price=50000.0, ask_price=50002.0, bid_size=10.0, ask_size=10.0)
    q2 = _make_quote("FEED_2", bid_price=50000.0, ask_price=50001.0, bid_size=5.0, ask_size=5.0)

    reconciler.reconcile(q1)
    dec = reconciler.reconcile(q2)
    assert dec is not None
    assert dec.chosen_source == "FEED_2"

    # Now compare FEED_2 with FEED_3 having same spread (1.0), but FEED_3 has larger depth (100 vs 10)
    q3 = _make_quote("FEED_3", bid_price=50000.0, ask_price=50001.0, bid_size=50.0, ask_size=50.0)
    dec3 = reconciler.reconcile(q3)
    assert dec3 is not None
    assert dec3.chosen_source == "FEED_3"


def test_quote_reconciliation_median_price_strategy():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"A": 0.80, "B": 0.90, "C": 0.70}
    reconciler = Reconciler(reliability=tracker, strategy=MedianPriceStrategy())

    # Midpoints:
    # A: (50000 + 50002)/2 = 50001.0
    # B: (50010 + 50012)/2 = 50011.0 (Median)
    # C: (50020 + 50022)/2 = 50021.0
    qa = _make_quote("A", bid_price=50000.0, ask_price=50002.0)
    qb = _make_quote("B", bid_price=50010.0, ask_price=50012.0)
    qc = _make_quote("C", bid_price=50020.0, ask_price=50022.0)

    reconciler.reconcile(qa)
    reconciler.reconcile(qb)
    dec = reconciler.reconcile(qc)

    assert dec is not None
    assert dec.reconciliation_type == "QUOTE"
    assert dec.chosen_source == "B"


def test_trade_and_quote_separation_in_reconciler():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"FEED_A": 0.95, "FEED_B": 0.90}
    reconciler = Reconciler(reliability=tracker)

    # Ingest trade on FEED_A, quote on FEED_B
    t_a = _make_trade("FEED_A", price=50000.0)
    q_b = _make_quote("FEED_B", bid_price=50000.0, ask_price=50002.0)

    # Neither should reach quorum because one is trade and one is quote
    assert reconciler.reconcile(t_a) is None
    assert reconciler.reconcile(q_b) is None

    # Now ingest quote on FEED_A -> reaches quote quorum
    q_a = _make_quote("FEED_A", bid_price=50000.0, ask_price=50001.0)
    dec_q = reconciler.reconcile(q_a)
    assert dec_q is not None
    assert dec_q.reconciliation_type == "QUOTE"
    assert dec_q.chosen_source == "FEED_A"

    # Ingest trade on FEED_B -> reaches trade quorum
    t_b = _make_trade("FEED_B", price=50000.5)
    dec_t = reconciler.reconcile(t_b)
    assert dec_t is not None
    assert dec_t.reconciliation_type == "TRADE"
    assert dec_t.chosen_source == "FEED_A"
