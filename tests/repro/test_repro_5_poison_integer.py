"""Repro 5: Verify that an event with a poison integer (e.g. sequence = 2**70) does not kill its neighboring events in a batch.

Expected behavior:
- Record boundary validation catches signed int64 overflow
- Event N (poison record) is quarantined
- Event N-1 and Event N+1 are successfully processed into canonical_events
- Batch bisection or pre-validation isolates the single bad event

Current defect:
gateway.normalize only checks `isinstance(sequence, int)`, which is True for arbitrarily large Python ints.
When `2**70` reaches native/ctypes or storage (signed int64 boundary), it raises OverflowError
and aborts the entire batch, killing healthy neighboring records!
"""

from __future__ import annotations

import pytest

from mdrap.models import QualityStatus, RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Poison integer (sequence = 2**70) escapes gateway and crashes entire batch, killing neighbors (Finding 5)",
)
def test_poison_integer_quarantined_and_neighbors_survive(tmp_path):
    db_path = str(tmp_path / "events.db")
    store = Store(db_path)
    pipeline = Pipeline(store=store, journal=False, async_writer=False)

    events = [
        # Record 0: Healthy
        RawEvent(
            source="POISON_REPRO",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.0,
                "quantity": 10.0,
                "exchange_ts": 1700000000.0,
                "sequence": 1,
            },
        ),
        # Record 1: Poison integer (exceeds signed int64, 2**70)
        RawEvent(
            source="POISON_REPRO",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 150.5,
                "quantity": 5.0,
                "exchange_ts": 1700000001.0,
                "sequence": 2**70,
            },
        ),
        # Record 2: Healthy
        RawEvent(
            source="POISON_REPRO",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "price": 151.0,
                "quantity": 20.0,
                "exchange_ts": 1700000002.0,
                "sequence": 3,
            },
        ),
    ]

    # Process micro-batch
    results = pipeline.process_batch(events)
    pipeline.finish()

    # The batch must return 3 results aligned to inputs
    assert len(results) == 3

    # Neighbor 0 must be VALID
    assert results[0] is not None
    assert results[0].quality_status == QualityStatus.VALID

    # Poison record 1 must be quarantined, NOT crashing the process
    assert results[1] is not None
    assert results[1].quality_status in (QualityStatus.INVALID, QualityStatus.SUSPICIOUS)

    # Neighbor 2 must be VALID
    assert results[2] is not None
    assert results[2].quality_status == QualityStatus.VALID

    # In database, canonical_events must contain the 2 healthy records
    canon_count = store.conn.execute(
        "SELECT count(*) FROM canonical_events WHERE source = 'POISON_REPRO'"
    ).fetchone()[0]
    assert canon_count == 2, f"Expected 2 healthy records saved, got {canon_count}"
