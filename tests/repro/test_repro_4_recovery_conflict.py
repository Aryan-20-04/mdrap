"""Repro 4: Verify that crash recovery is idempotent and does not produce false storage conflicts that fail finish().

Expected behavior:
- Recovering unprojected events that were already partially in the store is idempotent
- Recovery-replay does NOT increment false conflict counters
- Subsequent Pipeline.finish() succeeds without raising StorageConflictError

Current defect:
When recovery inserts events that already exist in canonical_events:
ON CONFLICT DO NOTHING results in `inserted < len(events)`, which unconditionally
increments `self.store.conflicts`.
Then Pipeline.finish() sees `store.conflicts > 0` and raises StorageConflictError!
"""

from __future__ import annotations

import json
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Crash recovery increments store.conflicts on idempotent replay, causing Pipeline.finish to raise StorageConflictError (Finding 4)",
)
def test_idempotent_recovery_does_not_trigger_finish_storage_conflict_error(tmp_path):
    db_path = str(tmp_path / "events.db")
    journal_path = f"{db_path}.journal"

    store = Store(db_path)
    ev = CanonicalEvent(
        event_id="crash_ev_001",
        instrument_id="NVDA",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        source="REPRO_4",
        price=450.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )

    # 1. Store already committed this event (simulating partial write before crash)
    store.write_canonical_batch([ev])
    store.commit()
    assert store.conflicts == 0

    # 2. But the journal also contained this event (because it was in flight)
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "canonical", "payload": ev.to_dict()}) + "\n")

    # 3. Process restarts, reopening Store, which triggers recovery
    recovered_store = Store(db_path)

    # Recovery should recognize this as idempotent replay, NOT a storage conflict!
    # Normal duplicate replay must not increment conflict metric
    assert getattr(recovered_store, "conflicts", 0) == 0, (
        f"False storage conflict recorded during recovery replay: conflicts={recovered_store.conflicts}"
    )

    # 4. Now a pipeline runs and completes normal work
    pipeline = Pipeline(store=recovered_store, journal=False, async_writer=False)
    pipeline.process_one(
        RawEvent(
            source="REPRO_4",
            payload={
                "instrument": "NVDA",
                "event_type": "TRADE",
                "price": 451.0,
                "quantity": 10.0,
                "exchange_ts": 1700000001.0,
                "sequence": 2,
            },
        )
    )

    # finish() MUST NOT raise StorageConflictError due to the earlier recovery
    pipeline.finish()
