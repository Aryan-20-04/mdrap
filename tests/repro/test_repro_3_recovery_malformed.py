"""Repro 3: Verify that crash recovery strictly parses journal entries, quarantines malformed records,
and does not truncate the journal in a finally block when errors exist.

Expected behavior:
- Malformed journal record is quarantined with investigative metadata
- Valid neighboring records in the journal are safely recovered
- No fabricated valid event is produced
- Journal is NOT blindly truncated if recovery encountered malformed/corrupted data

Current defect:
Store._recover_from_journal ignores malformed records, produces no quarantine record,
and blindly truncates the journal in a finally block:
`finally: with open(journal_path, "w"): pass`
"""

from __future__ import annotations

import json
import os
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Store._recover_from_journal does not quarantine malformed journal records and truncates in finally (Finding 3)",
)
def test_recovery_quarantines_malformed_records_and_preserves_journal(tmp_path):
    db_path = str(tmp_path / "events.db")
    journal_path = f"{db_path}.journal"

    valid_event = CanonicalEvent(
        event_id="valid_001",
        instrument_id="MSFT",
        event_type=EventType.TRADE,
        exchange_timestamp=1700000000.0,
        receive_timestamp=1700000000.001,
        source="EXCHANGE_A",
        price=350.0,
        quantity=50.0,
        quality_status=QualityStatus.VALID,
    )

    # Write a journal containing:
    # 1. Valid event
    # 2. Corrupt / malformed JSON entry
    # 3. Another valid event
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "canonical", "payload": valid_event.to_dict()}) + "\n")
        f.write("{MALFORMED_CORRUPTED_JSON_ENTRY: NOT_VALID\n")
        f.write(json.dumps({"type": "canonical", "payload": {
            "event_id": "valid_002",
            "instrument_id": "GOOG",
            "event_type": "TRADE",
            "exchange_timestamp": 1700000001.0,
            "receive_timestamp": 1700000001.002,
            "source": "EXCHANGE_A",
            "price": 140.0,
            "quantity": 25.0,
            "quality_status": "VALID",
        }}) + "\n")

    # Initializing Store triggers _recover_from_journal
    store = Store(db_path)

    # 1. Both valid events must be recovered
    canon_count = store.conn.execute("SELECT count(*) FROM canonical_events").fetchone()[0]
    assert canon_count == 2, f"Expected 2 recovered valid events, got {canon_count}"

    # 2. The malformed record MUST be quarantined into quarantine_events
    quar_count = store.conn.execute("SELECT count(*) FROM quarantine_events").fetchone()[0]
    assert quar_count >= 1, "Malformed journal record was not quarantined; evidence was silently dropped!"

    # 3. No fabricated records (e.g. unknown instrument / fabricated defaults)
    fabricated = store.conn.execute(
        "SELECT count(*) FROM canonical_events WHERE instrument_id = '' OR source = 'UNKNOWN'"
    ).fetchone()[0]
    assert fabricated == 0, "Fabricated events were inserted into canonical_events table!"

    # 4. Journal must NOT be truncated when corrupted records were encountered
    assert os.path.exists(journal_path), "Journal was deleted"
    assert os.path.getsize(journal_path) > 0, "Journal was blindly truncated in finally block despite malformed entries!"
