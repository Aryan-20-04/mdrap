"""Comprehensive test suite for Tier 2: Architecture, Extensibility, and Usability."""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import pytest

from mdrap.clock import FixedClock
from mdrap.client import AsyncClient
from mdrap.engine import Engine
from mdrap.ingestlog import IngestLog
from mdrap.log import (
    JSONLogFormatter,
    configure_logging,
    set_trace_context,
    get_trace_context,
)
from mdrap.models import RawEvent, QualityStatus
from mdrap.projection import (
    SQLiteProjection,
    AsyncProjectionWorker,
    rebuild_projection_from_wal,
)
from mdrap.service import MarketDataDaemon, StreamClient


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="mdrap_tier2_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_rebuild_projection_from_wal(temp_dir):
    wal_dir = os.path.join(temp_dir, "wal")
    db_path = os.path.join(temp_dir, "rebuilt.db")

    log = IngestLog(wal_dir)
    num_events = 25
    for i in range(num_events):
        raw = RawEvent(
            source="TEST_FEED",
            receive_timestamp=1700000000.0 + i,
            payload={
                "instrument": "MSFT",
                "event_type": "TRADE",
                "exchange_ts": 1700000000.0 + i,
                "sequence": i + 1,
                "price": 300.0 + i,
                "quantity": 10.0,
            },
        )
        log.append(raw)
    log.close()

    # Rebuild SQLite projection from the WAL
    proj = SQLiteProjection(db_path)
    count = rebuild_projection_from_wal(
        journal_dir=wal_dir,
        projection=proj,
        start_offset=0,
        batch_size=10,
    )

    assert count == num_events
    assert proj.checkpoint() == num_events - 1
    assert proj.count_canonical() == num_events
    rows = proj.query(
        "SELECT instrument_id, price FROM canonical_events ORDER BY sequence_number ASC"
    )
    assert len(rows) == num_events
    assert rows[0][0] == "MSFT"
    assert rows[0][1] == 300.0
    assert rows[-1][1] == 300.0 + (num_events - 1)
    proj.close()


def test_async_projection_worker_lag_tracking(temp_dir):
    db_path = os.path.join(temp_dir, "proj_lag.db")
    proj = SQLiteProjection(db_path)
    worker = AsyncProjectionWorker(
        projection=proj,
        max_queue_size=500,
        batch_size=50,
        flush_interval_sec=0.05,
    )

    engine = Engine()
    state = engine.create_initial_state()
    clock = FixedClock(1700000000.0)

    for i in range(20):
        raw = RawEvent(
            source="SRC1",
            receive_timestamp=1700000000.0 + (i * 0.01),
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": 1700000000.0 + (i * 0.01),
                "sequence": i + 1,
                "price": 150.0,
                "quantity": 5.0,
            },
        )
        state, dec = engine.step(state, raw, clock, offset=i)
        worker.enqueue(dec, offset=i)

    # Immediately check stats before flush
    stats_before = worker.stats()
    assert stats_before["highest_enqueued_offset"] == 19
    assert stats_before["items_enqueued"] == 20
    assert "lag_offsets" in stats_before
    assert "is_lagging" in stats_before

    worker.flush(timeout=2.0)
    stats_after = worker.stats()
    assert stats_after["items_committed"] == 20
    assert stats_after["last_committed_offset"] == 19
    assert stats_after["lag_offsets"] == 0
    assert not stats_after["is_lagging"]

    worker.close()
    proj.close()


def test_json_logging_and_trace_context(capsys):
    logger = logging.getLogger("mdrap.test.tracing")
    logger.propagate = True

    set_trace_context("trace_abc_123", "span_xyz_789")
    assert get_trace_context() == ("trace_abc_123", "span_xyz_789")

    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="mdrap.test.tracing",
        level=logging.WARNING,
        pathname="test_file.py",
        lineno=42,
        msg="Sample %s test message",
        args=("JSON",),
        exc_info=None,
    )

    json_str = formatter.format(record)
    parsed = json.loads(json_str)

    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "mdrap.test.tracing"
    assert parsed["message"] == "Sample JSON test message"
    assert parsed["lineno"] == 42
    assert parsed["trace_id"] == "trace_abc_123"
    assert parsed["span_id"] == "span_xyz_789"
    assert "timestamp" in parsed

    set_trace_context(None, None)


@pytest.mark.asyncio
async def test_async_client_context_and_methods(monkeypatch):
    client = AsyncClient(base_url="http://127.0.0.1:8000", api_key="test_key_123")
    assert not client._closed

    async with client as c:
        assert c._headers()["X-API-Key"] == "test_key_123"
        assert c._headers()["Authorization"] == "Bearer test_key_123"

        # Mock _request_sync to verify async call routing
        calls = []

        def mock_req(method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/v1/health":
                return {"status": "HEALTHY"}
            elif path == "/v1/metrics":
                return {"uptime_s": 100}
            elif path == "/v1/ingest":
                return {"status": "ACCEPTED", "event_id": "test_ev_1"}
            elif path == "/v1/ingest/batch":
                return {"status": "ACCEPTED", "count": len(payload or [])}
            return {}

        monkeypatch.setattr(c, "_request_sync", mock_req)

        h = await c.health()
        assert h["status"] == "HEALTHY"

        m = await c.get_metrics()
        assert m["uptime_s"] == 100

        sub_res = await c.submit({"instrument": "AAPL", "price": 150.0})
        assert sub_res["status"] == "ACCEPTED"

        batch_res = await c.submit_batch(
            [{"instrument": "AAPL"}, {"instrument": "MSFT"}]
        )
        assert batch_res["count"] == 2

    assert client._closed
    assert len(calls) == 4


def test_mtls_service_configuration():
    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=9999,
        allow_insecure_bind=True,
        require_client_cert=True,
    )
    assert daemon.require_client_cert is True

    client = StreamClient(
        host="127.0.0.1",
        port=9999,
        client_cert="dummy_client.crt",
        client_key="dummy_client.key",
    )
    assert client.use_tls is True
    assert client.client_cert == "dummy_client.crt"
    assert client.client_key == "dummy_client.key"
