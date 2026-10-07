"""Repro 6: Verify that non-dict payloads (None, [], [1], 'x') produce structured quarantine records and do not crash the pipeline with unhandled AttributeError.

Expected behavior:
- Non-dict payloads are validated at record ingress
- Structured quarantine record is created with MALFORMED reason
- Pipeline does not crash with AttributeError
- Neighboring events and subsequent events continue processing

Current defect:
gateway.normalize assumes raw.payload is a dict and executes `raw.payload.get(...)`.
If raw.payload is None, [1], or "x", Python raises AttributeError:
`AttributeError: 'NoneType' object has no attribute 'get'`
`AttributeError: 'list' object has no attribute 'get'`
`AttributeError: 'str' object has no attribute 'get'`
which escapes normalize() and crashes process_one()!
"""

from __future__ import annotations

import pytest

from mdrap.models import QualityStatus, RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store


@pytest.mark.xfail(
    strict=True,
    reason="Non-dict payloads (None, [], [1], 'x') cause unhandled AttributeError in gateway.normalize and crash the pipeline (Finding 6)",
)
@pytest.mark.parametrize("bad_payload", [None, [], [1], "x", 12345])
def test_non_dict_payload_quarantines_safely_without_crashing(tmp_path, bad_payload):
    db_path = str(tmp_path / "events.db")
    store = Store(db_path)
    pipeline = Pipeline(store=store, journal=False, async_writer=False)

    raw = RawEvent(
        source="NON_DICT_REPRO",
        payload=bad_payload,  # Non-dict payload
        receive_timestamp=1700000000.0,
    )

    # Must NOT raise AttributeError! Must return a quarantined CanonicalEvent
    try:
        result = pipeline.process_one(raw)
    except AttributeError as err:
        pytest.fail(f"Pipeline crashed with unhandled AttributeError on non-dict payload: {err}")

    assert result is not None, "Pipeline dropped non-dict event silently instead of returning quarantine record"
    assert result.quality_status in (QualityStatus.INVALID, QualityStatus.SUSPICIOUS)

    pipeline.finish()

    # The record must be durably recorded in quarantine_events
    quar_count = store.conn.execute(
        "SELECT count(*) FROM quarantine_events WHERE source = 'NON_DICT_REPRO'"
    ).fetchone()[0]
    assert quar_count == 1, "Non-dict payload was not recorded in quarantine_events table"
