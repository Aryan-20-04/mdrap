"""Unit & Integration Tests for MDRAP Market Data Reliability & Assurance Platform.

Verifies:
1. Event Provenance & Cryptographic Lineage (§3)
2. Connector Telemetry & Standardized State Tracking (§2)
3. Data Quality Incident Management & Timeline Correlation (§4)
4. Deterministic Incident Replay & Bitwise State Parity (§5)
5. Connector Conformance Certification Framework (§6)
"""

import json
import os
import tempfile
import time
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.provenance import (
    EventProvenance,
    ProvenanceTracker,
    compute_payload_hash,
    create_provenance,
)
from mdrap.ws_feed import ConnectorState, ProviderTelemetry, WebSocketFeedManager
from mdrap.incidents import DataQualityIncident, IncidentManager
from mdrap.replay import IncidentReplayEngine, IncidentReplayResult
from mdrap.chaos import ConnectorCertificationSuite, ConnectorCertificationReport
from mdrap.storage import Store


def test_event_provenance_hashing_and_lineage():
    raw = RawEvent(
        source="BINANCE",
        payload={"instrument": "BTC/USD", "bid": 73200.0, "ask": 73201.0},
        receive_timestamp=1729000000.0,
        raw_id="raw-binance-001",
        wire='{"e":"depth","b":73200.0,"a":73201.0}',
    )
    canon = CanonicalEvent(
        event_id="evt-binance-001",
        instrument_id="BTC/USD",
        event_type=EventType.QUOTE,
        exchange_timestamp=1728999999.9,
        receive_timestamp=1729000000.0,
        source="BINANCE",
        bid_price=73200.0,
        ask_price=73201.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id="raw-binance-001",
    )

    prov = create_provenance(raw, canon, channel="ORDERBOOK_L1", data_mode="LIVE")

    assert prov.event_id == "evt-binance-001"
    assert prov.provider == "BINANCE"
    assert prov.data_mode == "LIVE"
    assert prov.payload_sha256 == compute_payload_hash(raw.wire)
    assert len(prov.lineage_hash) == 64  # SHA-256 hex string

    tracker = ProvenanceTracker(max_records=100)
    tracker.record(prov)
    retrieved = tracker.get("evt-binance-001")
    assert retrieved is not None
    assert retrieved.lineage_hash == prov.lineage_hash

    evidence = tracker.export_evidence("evt-binance-001")
    assert evidence["verification_status"] == "VERIFIED_TAMPER_EVIDENT"
    assert evidence["integrity"]["payload_sha256"] == prov.payload_sha256


def test_connector_telemetry_contract():
    mgr = WebSocketFeedManager(symbols=["BTC/USD"], venues=["BINANCE", "BYBIT"])
    telemetry = mgr.get_provider_telemetry()

    assert "BINANCE" in telemetry
    assert "BYBIT" in telemetry
    binance_tel = telemetry["BINANCE"]
    assert isinstance(binance_tel, ProviderTelemetry)
    assert binance_tel.state in (ConnectorState.DISCONNECTED, ConnectorState.CONNECTING)
    assert binance_tel.data_mode == "LIVE"
    assert binance_tel.active_subscriptions == ["BTC/USD"]


def test_data_quality_incident_lifecycle():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        mgr = IncidentManager(db_path=db_path)
        ev = {
            "source": "BYBIT",
            "instrument_id": "BTC/USD",
            "event_id": "evt-gap-001",
            "receive_timestamp": time.time(),
        }

        # 1. Record anomaly
        inc = mgr.record_anomaly(
            ev, "SEQUENCE_GAP", "Detected 5 missing sequence packets", severity="HIGH"
        )
        assert inc.incident_id.startswith("inc-")
        assert inc.provider == "BYBIT"
        assert inc.anomaly_type == "SEQUENCE_GAP"
        assert inc.is_active is True

        # 2. Query incidents
        active_list = mgr.query_incidents(limit=10, provider="BYBIT")
        assert len(active_list) == 1
        assert active_list[0].incident_id == inc.incident_id

        # 3. Close incident
        mgr.close_incident("BYBIT", "BTC/USD", "SEQUENCE_GAP")
        closed_inc = mgr.get_incident(inc.incident_id)
        assert closed_inc is not None
        assert closed_inc.is_active is False
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def test_deterministic_incident_replay():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        store = Store(db_path)
        # Populate with sequential canonical events
        t0 = 1729000000.0
        events = [
            CanonicalEvent(
                event_id="evt-rep-1",
                instrument_id="BTC/USD",
                event_type=EventType.QUOTE,
                exchange_timestamp=t0 + 0.1,
                receive_timestamp=t0 + 0.1,
                source="BYBIT",
                sequence_number=100,
                bid_price=73200.0,
                ask_price=73201.0,
                bid_size=1.0,
                ask_size=1.0,
                raw_id="raw-1",
            ),
            CanonicalEvent(
                event_id="evt-rep-2",
                instrument_id="BTC/USD",
                event_type=EventType.QUOTE,
                exchange_timestamp=t0 + 0.2,
                receive_timestamp=t0 + 0.2,
                source="BYBIT",
                sequence_number=105,  # Injected gap
                bid_price=73205.0,
                ask_price=73206.0,
                bid_size=1.5,
                ask_size=1.5,
                raw_id="raw-2",
            ),
        ]
        store.write_canonical_batch(events)
        store.close()

        # Record incident covering these events
        mgr = IncidentManager(db_path=db_path)
        inc = mgr.record_anomaly(events[1], "SEQUENCE_GAP", "Sequence gap 100 to 105")

        # Run Replay
        replay_engine = IncidentReplayEngine(db_path=db_path)
        res1 = replay_engine.replay_incident(inc.incident_id)

        assert isinstance(res1, IncidentReplayResult)
        assert res1.is_reproducible is True
        assert res1.events_replayed >= 1
        assert "SEQUENCE_GAP" in res1.reproduced_anomalies
        assert len(res1.replayed_state_hash) == 64

        # Run Replay a second time - verify bitwise identical state hash
        res2 = replay_engine.replay_incident(inc.incident_id)
        assert res1.replayed_state_hash == res2.replayed_state_hash
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def test_connector_certification_suite():
    suite = ConnectorCertificationSuite()
    report = suite.certify_connector(provider="BINANCE")

    assert isinstance(report, ConnectorCertificationReport)
    assert report.provider == "BINANCE"
    assert report.scenarios_run == 4
    assert report.scenarios_passed == 4
    assert report.scenarios_failed == 0
    assert report.final_verdict == "PASS"
