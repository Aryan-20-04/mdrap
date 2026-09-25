import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from models import CanonicalEvent, EventType, QualityStatus
from fastpath import FastQualityEngine, HAS_FASTPATH
from quality import QualityConfig

pytestmark = pytest.mark.skipif(
    not HAS_FASTPATH, reason="Native fastpath library not available"
)


def make_event(**overrides):
    base = dict(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=0.0,
        source="FEED_A",
        sequence_number=1,
        price=150.0,
        quantity=10.0,
    )
    base.update(overrides)
    return CanonicalEvent(**base)


def test_multi_engine_dedup_and_sequence_isolation():
    cfg = QualityConfig(
        staleness_threshold_s=10.0, price_anomaly_stddev=4.0, price_window=50
    )
    eng1 = FastQualityEngine(cfg)
    eng2 = FastQualityEngine(cfg)

    try:
        ev1 = make_event(sequence_number=1, price=150.0)
        res1 = eng1.evaluate(ev1)
        assert res1.quality_status == QualityStatus.VALID

        ev1_dup = make_event(sequence_number=1, price=150.0)
        res1_dup = eng1.evaluate(ev1_dup)
        assert res1_dup.quality_status == QualityStatus.INVALID
        assert "DUPLICATE" in res1_dup.reasons

        ev2 = make_event(sequence_number=1, price=150.0)
        res2 = eng2.evaluate(ev2)
        assert res2.quality_status == QualityStatus.VALID
        assert "DUPLICATE" not in res2.reasons

        ev1_seq2 = make_event(
            sequence_number=2,
            exchange_timestamp=1000.1,
            receive_timestamp=1000.101,
            price=150.1,
        )
        assert eng1.evaluate(ev1_seq2).quality_status == QualityStatus.VALID

        ev2_seq5 = make_event(
            sequence_number=5,
            exchange_timestamp=1000.2,
            receive_timestamp=1000.201,
            price=150.2,
        )
        res2_seq = eng2.evaluate(ev2_seq5)
        assert res2_seq.quality_status == QualityStatus.SUSPICIOUS
        assert "SEQUENCE_GAP" in res2_seq.reasons

        ev1_seq3 = make_event(
            sequence_number=3,
            exchange_timestamp=1000.3,
            receive_timestamp=1000.301,
            price=150.15,
        )
        res1_seq = eng1.evaluate(ev1_seq3)
        assert res1_seq.quality_status == QualityStatus.VALID
        assert "SEQUENCE_GAP" not in res1_seq.reasons
    finally:
        eng1.close()
        eng2.close()


def test_multi_engine_price_stats_isolation():
    cfg = QualityConfig(
        staleness_threshold_s=10.0, price_anomaly_stddev=3.0, price_window=30
    )
    eng_low = FastQualityEngine(cfg)
    eng_high = FastQualityEngine(cfg)

    try:
        for i in range(25):
            eng_low.evaluate(
                make_event(
                    instrument_id="MSFT",
                    exchange_timestamp=1000.0 + i * 0.1,
                    receive_timestamp=1000.0 + i * 0.1 + 0.001,
                    sequence_number=i + 1,
                    price=100.0 + (i % 3) * 0.1,
                )
            )

        for i in range(25):
            eng_high.evaluate(
                make_event(
                    instrument_id="MSFT",
                    exchange_timestamp=1000.0 + i * 0.1,
                    receive_timestamp=1000.0 + i * 0.1 + 0.001,
                    sequence_number=i + 1,
                    price=500.0 + (i % 3) * 0.1,
                )
            )

        res_low = eng_low.evaluate(
            make_event(
                instrument_id="MSFT",
                exchange_timestamp=1003.0,
                receive_timestamp=1003.001,
                sequence_number=26,
                price=100.1,
            )
        )
        assert res_low.reasons == [], (
            f"Unexpected reasons on res_low: {res_low.reasons}"
        )
        assert res_low.quality_status == QualityStatus.VALID

        res_high = eng_high.evaluate(
            make_event(
                instrument_id="MSFT",
                exchange_timestamp=1010.0,
                receive_timestamp=1010.001,
                sequence_number=26,
                price=100.2,
            )
        )
        assert res_high.quality_status == QualityStatus.SUSPICIOUS
        assert "PRICE_ANOMALY" in res_high.reasons
    finally:
        eng_low.close()
        eng_high.close()


def test_engine_reset_isolation():
    cfg = QualityConfig(staleness_threshold_s=10.0)
    eng1 = FastQualityEngine(cfg)
    eng2 = FastQualityEngine(cfg)

    try:
        ev = make_event(instrument_id="GOOG", sequence_number=1, price=200.0)
        eng1.evaluate(ev)
        eng2.evaluate(ev)

        eng1.reset()

        # Create fresh event with same fields to re-evaluate
        ev_new1 = make_event(instrument_id="GOOG", sequence_number=1, price=200.0)
        res1 = eng1.evaluate(ev_new1)
        assert res1.quality_status == QualityStatus.VALID

        ev_new2 = make_event(instrument_id="GOOG", sequence_number=1, price=200.0)
        res2 = eng2.evaluate(ev_new2)
        assert res2.quality_status == QualityStatus.INVALID
        assert "DUPLICATE" in res2.reasons
    finally:
        eng1.close()
        eng2.close()
