"""Tests for pluggable reconciliation strategies in MDRAP (Item 2.1)."""

import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason
from mdrap.reconciliation import (
    CanonicalDecision,
    HighestReliabilityStrategy,
    MedianPriceStrategy,
    ReconciliationConfig,
    Reconciler,
    ReconciliationStrategy,
    ReliabilityTracker,
)


def _make_event(
    source: str, price: float, ts: float = 1000.0, event_id: str = ""
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id or f"ev_{source}_{price}",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=source,
        price=price,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )


def test_highest_reliability_strategy_selection():
    tracker = ReliabilityTracker()
    tracker._cached_scores = {"FEED_A": 0.95, "FEED_B": 0.80, "FEED_C": 0.90}

    reconciler = Reconciler(
        reliability=tracker,
        strategy=HighestReliabilityStrategy(),
    )

    ev_b = _make_event("FEED_B", 150.0)
    ev_c = _make_event("FEED_C", 150.05)
    ev_a = _make_event("FEED_A", 150.02)

    assert reconciler.reconcile(ev_b) is None  # Quorum not reached (< 2 feeds)

    dec = reconciler.reconcile(ev_c)
    assert dec is not None
    # FEED_C (0.90) > FEED_B (0.80)
    assert dec.chosen_source == "FEED_C"
    assert dec.disagreement is False

    dec_a = reconciler.reconcile(ev_a)
    assert dec_a is not None
    # FEED_A (0.95) is highest
    assert dec_a.chosen_source == "FEED_A"
    assert dec_a.chosen_event_id == ev_a.event_id


def test_median_price_strategy_resists_rogue_outliers():
    tracker = ReliabilityTracker()
    # Even if rogue feed has highest score, median price strategy picks median
    tracker._cached_scores = {"FEED_ROGUE": 0.99, "FEED_B": 0.85, "FEED_C": 0.90}

    reconciler = Reconciler(
        reliability=tracker,
        strategy=MedianPriceStrategy(),
        cfg=ReconciliationConfig(disagreement_pct_threshold=0.01),
    )

    ev_b = _make_event("FEED_B", 100.0)
    ev_c = _make_event("FEED_C", 100.1)
    ev_rogue = _make_event("FEED_ROGUE", 250.0)  # Huge outlier/spike

    reconciler.reconcile(ev_b)
    reconciler.reconcile(ev_c)
    dec = reconciler.reconcile(ev_rogue)

    assert dec is not None
    # Candidates: 100.0 (B), 100.1 (C), 250.0 (ROGUE) -> median is 100.1 (C)
    assert dec.chosen_source == "FEED_C"
    assert dec.chosen_event_id == ev_c.event_id
    assert dec.disagreement is True  # Outlier triggers cross-feed disagreement
    assert Reason.CROSS_FEED_DISAGREEMENT.value in dec.quality_reasons


def test_custom_strategy_injection():
    class LowestPriceStrategy:
        """Custom user strategy selecting the lowest price."""

        def select(
            self,
            instrument_id: str,
            candidates: dict[str, tuple[CanonicalEvent, float]],
            scores: dict[str, float],
            agreement_window_s: float,
            disagreement_pct_threshold: float,
            blocked_sources: set[str] | None = None,
            market_now: float | None = None,
        ) -> CanonicalDecision | None:
            active = [
                (src, ev)
                for src, (ev, ts) in candidates.items()
                if ev.price is not None
            ]
            if len(active) < 2:
                return None
            active.sort(key=lambda item: item[1].price)
            chosen_src, chosen_ev = active[0]
            return CanonicalDecision(
                instrument_id=instrument_id,
                chosen_source=chosen_src,
                chosen_event_id=chosen_ev.event_id,
                disagreement=False,
                reason="custom lowest price",
                competing_sources=[s for s, _ in active],
            )

    custom_strat = LowestPriceStrategy()
    assert isinstance(custom_strat, ReconciliationStrategy)

    reconciler = Reconciler(strategy=custom_strat)
    reconciler.reconcile(_make_event("FEED_HIGH", 200.0))
    dec = reconciler.reconcile(_make_event("FEED_LOW", 195.0))

    assert dec is not None
    assert dec.chosen_source == "FEED_LOW"
    assert dec.reason == "custom lowest price"


def test_zero_mutation_of_cached_canonical_events():
    reconciler = Reconciler()
    ev1 = _make_event("FEED_1", 100.0)
    ev2 = _make_event("FEED_2", 200.0)  # Extreme disagreement

    orig_reasons_1 = list(ev1.reasons)
    orig_reasons_2 = list(ev2.reasons)

    reconciler.reconcile(ev1)
    dec = reconciler.reconcile(ev2)

    assert dec is not None
    assert dec.disagreement is True
    # Invariant: reconcile must NEVER mutate reasons on the cached event object
    assert ev1.reasons == orig_reasons_1
    assert ev2.reasons == orig_reasons_2
    assert Reason.CROSS_FEED_DISAGREEMENT.value in dec.quality_reasons
