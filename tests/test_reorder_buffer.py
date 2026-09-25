import time
import pytest
from models import CanonicalEvent, EventType, QualityStatus, Reason, RawEvent
from quality import QualityConfig, QualityEngine
from pipeline import Pipeline
from storage import Store


def _create_event(
    seq: int,
    price: float = 100.0,
    instrument: str = "AAPL",
    source: str = "FEED1",
    ts: float = 1000.0,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=f"evt_{seq}",
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=ts + (seq * 0.001),
        receive_timestamp=ts + (seq * 0.001) + 0.001,
        processing_timestamp=0.0,
        source=source,
        sequence_number=seq,
        price=price,
        quantity=10.0,
    )


def test_reorder_buffer_disabled_by_default():
    cfg = QualityConfig(reorder_window_s=0.0)
    engine = QualityEngine(cfg)

    ev1 = engine.evaluate(_create_event(1))
    assert ev1 is not None and ev1.quality_status == QualityStatus.VALID

    # Jump to 3 (missing 2)
    ev3 = engine.evaluate(_create_event(3))
    assert ev3 is not None
    assert ev3.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in ev3.reasons

    # Late arrival 2
    ev2 = engine.evaluate(_create_event(2))
    assert ev2 is not None
    assert ev2.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.OUT_OF_ORDER.value in ev2.reasons


def test_reorder_buffer_repairs_packet_jitter():
    # 50ms jitter window
    cfg = QualityConfig(reorder_window_s=0.05, reorder_max_slots=16)
    engine = QualityEngine(cfg)

    # 1 arrives
    ev1 = engine.evaluate(_create_event(1))
    assert ev1 is not None and ev1.quality_status == QualityStatus.VALID

    # 2 arrives
    ev2 = engine.evaluate(_create_event(2))
    assert ev2 is not None and ev2.quality_status == QualityStatus.VALID

    # 4 arrives before 3 (jitter)
    ev4_held = engine.evaluate(_create_event(4))
    assert ev4_held is None  # Held in reorder buffer!
    assert engine.reorder_repaired_total == 0

    # 3 arrives within window
    ev3 = engine.evaluate(_create_event(3))
    assert ev3 is not None and ev3.quality_status == QualityStatus.VALID

    # Drain buffer: 4 should now be repaired and released as VALID!
    drained = engine.drain_expired()
    assert len(drained) == 1
    ev4 = drained[0]
    assert ev4.sequence_number == 4
    assert ev4.quality_status == QualityStatus.VALID
    assert Reason.SEQUENCE_GAP.value not in ev4.reasons
    assert engine.reorder_repaired_total == 1

    # 5 arrives: contiguous after 4!
    ev5 = engine.evaluate(_create_event(5))
    assert ev5 is not None and ev5.quality_status == QualityStatus.VALID


def test_reorder_buffer_expires_on_true_gap():
    # Very short 10ms window
    cfg = QualityConfig(reorder_window_s=0.01, reorder_max_slots=16)
    engine = QualityEngine(cfg)

    engine.evaluate(_create_event(1))
    held = engine.evaluate(_create_event(3))
    assert held is None

    # Wait for window to expire
    time.sleep(0.02)

    # Drain expired: 3 was genuinely gapped (2 never arrived)
    drained = engine.drain_expired()
    assert len(drained) == 1
    ev3 = drained[0]
    assert ev3.sequence_number == 3
    assert ev3.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.SEQUENCE_GAP.value in ev3.reasons
    assert engine.reorder_expired_total == 1


def test_reorder_buffer_pipeline_integration():
    store = Store(":memory:")
    cfg = QualityConfig(reorder_window_s=0.05, reorder_max_slots=16)
    engine = QualityEngine(cfg)
    pipeline = Pipeline(store=store, quality=engine)

    def make_raw(seq):
        return RawEvent(
            source="FEED1",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10.0,
                "sequence": seq,
                "exchange_ts": 1000.0 + seq,
            },
            receive_timestamp=1000.0 + seq,
            raw_id=f"r_{seq}",
        )

    # Ingest 1, 3, 2, 4
    r1 = pipeline.process_one(make_raw(1))
    assert r1 is not None

    r3 = pipeline.process_one(make_raw(3))
    assert r3 is None  # Held in buffer

    r2 = pipeline.process_one(make_raw(2))
    assert r2 is not None

    r4 = pipeline.process_one(make_raw(4))
    assert r4 is not None

    pipeline.finish()

    # Verify all 4 are stored in canonical and none in quarantine for SEQUENCE_GAP!
    canon = store.latest("AAPL", limit=10)
    assert len(canon) == 4
    assert engine.reorder_repaired_total == 1
    quar = store.quarantine_sample()
    seq_gaps = [q for q in quar if "SEQUENCE_GAP" in str(q)]
    assert len(seq_gaps) == 0
