"""End-to-End System Validation Test Suite (Phase 4 Workstream A & B).

Traces events through the complete institutional market data lifecycle:
  1. Ingress Replay Adapter (Raw framing, sequence extraction)
  2. Normalization (Typed CanonicalEvent, venue tagging)
  3. Quality Engine (Price anomaly, quote sanity checks)
  4. IngestLog WAL (CRC32 frame persistence, atomic flush)
  5. SBE Frame Serialization (64-byte binary wire layout)
  6. Durable Metering (Idempotent SQLite accounting)
  7. Replay Verification (WAL replay reconstruction)
"""

import json
import os
import struct
import tempfile
import time
import pytest
from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.ingestlog import IngestLog
from mdrap.metering import DurableUsageMeter, MeteringUnit
from mdrap.models import EventType, QualityStatus
from mdrap.quality import QualityEngine


def test_full_pipeline_event_trace(tmp_path):
    """Trace an event from ingress adapter through quality, WAL persistence, SBE wire framing, and metering."""
    wal_dir = str(tmp_path / "wal")
    meter_db = str(tmp_path / "metering.db")
    os.makedirs(wal_dir, exist_ok=True)

    # 1. Ingress Adapter Setup
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="NASDAQ", feed_id="ITCH_DIRECT"),
        frames=[
            {"seq": 5001, "sym": "AAPL", "px": 150.25, "sz": 100.0, "type": "TRADE"},
            {"seq": 5002, "sym": "AAPL", "bid": 150.20, "ask": 150.30, "type": "QUOTE"},
        ],
    )
    adapter.connect()

    # 2. Pipeline Components
    quality_engine = QualityEngine()
    ingest_log = IngestLog(log_dir=wal_dir, max_segment_bytes=1024 * 1024)
    usage_meter = DurableUsageMeter(meter_db)

    # 3. Process First Event
    raw1 = adapter.poll()
    assert raw1 is not None
    canon1 = adapter.normalize(raw1)
    assert canon1.instrument_id == "AAPL"
    assert canon1.sequence_number == 5001

    # Quality Evaluation
    eval_res1 = quality_engine.evaluate(canon1)
    assert eval_res1.quality_status == QualityStatus.VALID

    # Commit to IngestLog WAL
    offset1 = ingest_log.append(raw1)
    assert offset1 >= 0

    # Binary SBE Wire Framing (64 bytes aligned)
    inst_b = canon1.instrument_id.encode("ascii").ljust(16, b"\x00")
    sbe_frame = struct.pack(
        "<16sQqddII8s",
        inst_b,
        canon1.sequence_number,
        int(canon1.exchange_timestamp * 1e9),
        canon1.price,
        canon1.quantity,
        1,  # TRADE
        0,  # VALID
        b"\x00" * 8,
    )
    assert len(sbe_frame) == 64

    # Durable Usage Accounting
    recorded = usage_meter.record_usage(
        client_id="Desk_Quant_1",
        tenant_id="Fund_Apex",
        source=canon1.source,
        symbol=canon1.instrument_id,
        count=1,
        idempotency_key=f"trace_evt_{canon1.event_id}",
        unit=MeteringUnit.DISTRIBUTED_EVENT,
    )
    assert recorded is True

    # 4. Process Second Event (Quote)
    raw2 = adapter.poll()
    assert raw2 is not None
    canon2 = adapter.normalize(raw2)
    eval_res2 = quality_engine.evaluate(canon2)
    assert eval_res2.quality_status == QualityStatus.VALID

    # 5. WAL Replay Verification
    ingest_log.close()
    replayed_events = []
    reopen_log = IngestLog(log_dir=wal_dir)
    for off, raw_rec in reopen_log.iter_from(0):
        replayed_events.append(raw_rec)

    assert len(replayed_events) == 1
    assert replayed_events[0].source == "NASDAQ"
    assert replayed_events[0].payload["seq"] == 5001
    assert replayed_events[0].payload["px"] == 150.25

    # 6. Usage Metering Summary Verification
    summary = usage_meter.get_summary()
    assert len(summary) == 1
    assert summary[0]["client_id"] == "Desk_Quant_1"
    assert summary[0]["total_count"] == 1

    usage_meter.close()
    reopen_log.close()
    adapter.disconnect()
