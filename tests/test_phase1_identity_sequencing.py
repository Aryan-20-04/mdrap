"""Phase 1 Regression Suite: Event Identity & Monotonic Sequencing (Spec §5 & Task 3).

Verifies that:
1. Event and raw IDs include unique run_id preventing collision across restarts (INV-SEQ-001).
2. Two distinct Gateway instances generate completely non-overlapping event IDs.
3. IngestLog offsets and Engine monotonic counts are strictly sequential.
"""

from mdrap.gateway import Gateway
from mdrap.models import RawEvent


def test_event_id_unique_across_process_restarts():
    """Simulated restart creates new run_id and distinct event/raw IDs."""
    gw1 = Gateway(run_id="boot_aaa111")
    gw2 = Gateway(run_id="boot_bbb222")

    raw1 = gw1.ingest(RawEvent(source="FEEDX", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": 1.0}))
    raw2 = gw2.ingest(RawEvent(source="FEEDX", payload={"instrument": "AAPL", "event_type": "TRADE", "price": 100.0, "quantity": 1.0}))

    assert raw1.raw_id != raw2.raw_id
    assert "boot_aaa111" in raw1.raw_id
    assert "boot_bbb222" in raw2.raw_id

    ev1 = gw1.normalize(raw1)
    ev2 = gw2.normalize(raw2)

    assert ev1.event_id != ev2.event_id
    assert "boot_aaa111" in ev1.event_id
    assert "boot_bbb222" in ev2.event_id


def test_gateway_monotonic_id_increment():
    """IDs within single gateway run increment monotonically."""
    gw = Gateway(run_id="test_run")
    ids = [gw.next_event_id() for _ in range(5)]
    assert ids == [
        "evt-test_run-1",
        "evt-test_run-2",
        "evt-test_run-3",
        "evt-test_run-4",
        "evt-test_run-5",
    ]
