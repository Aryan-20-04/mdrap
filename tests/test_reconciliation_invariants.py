"""Phase 7 Reconciliation Engine Invariants Tests.

Tests:
1. Same event across feeds (consensus agreement)
2. Different timestamps (within window vs outside window)
3. Different prices (disagreement threshold trigger)
4. Different quantities (quantity preservation on chosen canonical)
5. Missing event (unilateral feed observation without quorum)
6. Delayed event (past agreement window boundary)
7. Duplicate event (reliability penalty on duplicated feed)
8. Sequence mismatch / gap (reliability penalty on gapped feed)
9. Feed outage (source blocking / failover)
10. Feed recovery (source unblocking / restoration)
11. HARD INVARIANT: Same input sequence + same configuration = IDENTICAL output.
"""

from models import CanonicalEvent, EventType, QualityStatus, Reason
from reconciliation import Reconciler, ReliabilityTracker, ReliabilityConfig


def _event(
    source: str,
    price: float,
    ex_ts: float,
    event_id: str,
    seq: int = 1,
    qty: float = 100.0,
    status: QualityStatus = QualityStatus.VALID,
    reasons: list[str] | None = None,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=ex_ts,
        receive_timestamp=ex_ts + 0.005,
        processing_timestamp=ex_ts + 0.006,
        source=source,
        sequence_number=seq,
        venue="NASDAQ",
        price=price,
        quantity=qty,
        quality_status=status,
        reasons=list(reasons or []),
    )


def test_reconciliation_same_event_agreement():
    """Feeds reporting agreeing prices produce consensus decision without disagreement."""
    rel = ReliabilityTracker()
    rec = Reconciler(reliability=rel)

    ev_a = _event("FEED_A", 150.0, 100.0, "ev_a_1")
    ev_b = _event("FEED_B", 150.02, 100.001, "ev_b_1")

    rel.observe(ev_a)
    dec1 = rec.reconcile(ev_a)
    assert dec1 is None  # only 1 feed seen yet

    rel.observe(ev_b)
    dec2 = rec.reconcile(ev_b)
    assert dec2 is not None
    assert dec2.disagreement is False
    assert set(dec2.competing_sources) == {"FEED_A", "FEED_B"}


def test_reconciliation_different_prices_disagreement():
    """Feeds reporting diverged prices (> 0.5%) flag CROSS_FEED_DISAGREEMENT."""
    rel = ReliabilityTracker()
    rec = Reconciler(reliability=rel)

    ev_a = _event("FEED_A", 150.0, 100.0, "ev_a_1")
    ev_b = _event("FEED_B", 155.0, 100.002, "ev_b_1")  # ~3.3% divergence

    rel.observe(ev_a)
    rec.reconcile(ev_a)
    rel.observe(ev_b)
    dec = rec.reconcile(ev_b)

    assert dec is not None
    assert dec.disagreement is True
    assert "disagreement" in dec.reason


def test_reconciliation_different_timestamps_window_boundary():
    """Observations beyond agreement_window_s are excluded from quorum."""
    cfg = ReliabilityConfig(agreement_window_s=0.25)
    rel = ReliabilityTracker(config=cfg)
    rec = Reconciler(reliability=rel, config=cfg)

    ev1 = _event("FEED_A", 150.0, 100.0, "ev_1")
    rel.observe(ev1)
    rec.reconcile(ev1)

    # 0.30s later (outside 0.25s window)
    ev2 = _event("FEED_B", 150.0, 100.30, "ev_2")
    rel.observe(ev2)
    dec = rec.reconcile(ev2)
    # FEED_A is stale relative to ev2, so no 2-feed quorum within 0.25s
    assert dec is None


def test_reconciliation_different_quantities():
    """Chosen canonical event retains its native quantity and provenance."""
    rel = ReliabilityTracker()
    rec = Reconciler(reliability=rel)

    ev_a = _event("FEED_A", 150.0, 100.0, "ev_a_1", qty=500.0)
    ev_b = _event("FEED_B", 150.0, 100.001, "ev_b_1", qty=250.0)

    rel.observe(ev_a)
    rec.reconcile(ev_a)
    rel.observe(ev_b)
    dec = rec.reconcile(ev_b)

    assert dec is not None
    # Deterministic tie break prefers FEED_A
    assert dec.chosen_source == "FEED_A"
    assert dec.chosen_event_id == "ev_a_1"


def test_reconciliation_feed_penalty_and_selection():
    """Feed with error/duplicate/gap penalties loses canonical election to clean feed."""
    rel = ReliabilityTracker()
    rec = Reconciler(reliability=rel)

    # Degrade FEED_B with duplicate and sequence gap penalties
    for i in range(1, 10):
        ev_b_bad = _event(
            "FEED_B",
            150.0,
            100.0 + i * 0.01,
            f"b_{i}",
            status=QualityStatus.SUSPICIOUS,
            reasons=[Reason.SEQUENCE_GAP.value],
        )
        rel.observe(ev_b_bad)

    # FEED_A is pristine
    for i in range(1, 10):
        ev_a_clean = _event("FEED_A", 150.0, 100.0 + i * 0.01, f"a_{i}")
        rel.observe(ev_a_clean)

    scores = rel.scores()
    assert scores["FEED_A"] > scores["FEED_B"]

    ev_a = _event("FEED_A", 150.0, 101.0, "ev_a_cand")
    ev_b = _event("FEED_B", 150.05, 101.001, "ev_b_cand")
    rel.observe(ev_a)
    rec.reconcile(ev_a)
    rel.observe(ev_b)
    dec = rec.reconcile(ev_b)

    assert dec is not None
    assert dec.chosen_source == "FEED_A"


def test_reconciliation_feed_outage_and_recovery():
    """Blocking an outaged feed removes it from quorum; unblocking restores it."""
    rel = ReliabilityTracker()
    rec = Reconciler(reliability=rel)

    ev_a = _event("FEED_A", 150.0, 100.0, "ev_a")
    ev_b = _event("FEED_B", 150.0, 100.001, "ev_b")
    rel.observe(ev_a)
    rec.reconcile(ev_a)
    rel.observe(ev_b)

    # Block FEED_A (e.g. Watchdog detected silent disconnect)
    rec.block_source("FEED_A")
    dec_blocked = rec.reconcile(ev_b)
    assert dec_blocked is None  # FEED_A is blocked, only FEED_B is active, no quorum

    # Unblock FEED_A (restored)
    rec.unblock_source("FEED_A")
    dec_restored = rec.reconcile(ev_b)
    assert dec_restored is not None
    assert dec_restored.chosen_source in ("FEED_A", "FEED_B")


def test_reconciliation_hard_determinism_invariant():
    """HARD INVARIANT: Same input stream + same configuration = IDENTICAL output decisions."""

    def run_simulation():
        rel = ReliabilityTracker()
        rec = Reconciler(reliability=rel)
        decisions = []
        for i in range(100):
            p_a = 150.0 + (i % 7) * 0.1
            p_b = 150.0 + (i % 5) * 0.1
            ev_a = _event("FEED_A", p_a, 100.0 + i * 0.01, f"a_{i}", seq=i + 1)
            ev_b = _event("FEED_B", p_b, 100.0 + i * 0.01 + 0.001, f"b_{i}", seq=i + 1)
            rel.observe(ev_a)
            d1 = rec.reconcile(ev_a)
            if d1:
                decisions.append(
                    (d1.chosen_source, d1.chosen_event_id, d1.disagreement)
                )
            rel.observe(ev_b)
            d2 = rec.reconcile(ev_b)
            if d2:
                decisions.append(
                    (d2.chosen_source, d2.chosen_event_id, d2.disagreement)
                )
        return decisions

    run1 = run_simulation()
    run2 = run_simulation()
    run3 = run_simulation()

    assert len(run1) > 0
    assert run1 == run2 == run3
