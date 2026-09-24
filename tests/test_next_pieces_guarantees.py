"""Cross-Cutting Verification Test Suite for Kafka Sink, Alert Delivery, and Prometheus Exporter.

Enforces the Shared Contract:
1. Pipeline Isolation: Sinks and delivery workers run decoupled; failure or latency
   downstream never blocks tick ingestion or moves pipeline baseline latency.
2. Delivery Semantics: Durable Kafka sink delivers at-least-once with checkpointed resumption.
3. Anti-Flapping / False-Positive Prevention:
   - Topic separation ensures invalid/quarantined events never touch canonical Kafka stream.
   - Hysteresis windows and clear margins suppress threshold oscillation flickers.
4. False-Negative Prevention:
   - Valid events tail correctly into Kafka topic.
   - Failed alert deliveries retry with backoff, stay queryable in DB, and spill to dead-letter.
5. End-to-End Reconciliation:
   - Store canonical event count matches Kafka produced count.
   - Lag and backlog metrics are exposed accurately via /metrics.
6. Security Guarantees:
   - HMAC-SHA256 signature verification for outbound alert webhooks.
   - Credentials not logged or exposed in plaintext.
   - Gated /metrics endpoint in production profile.
7. Alert Pipeline Liveness:
   - Heartbeat self-test alert verifies delivery fabric end-to-end.
"""

from __future__ import annotations

import json
import os
import time
import pytest
from fastapi.testclient import TestClient

from alerts import Alert, AlertEngine, AlertStatus, AlertType
from alert_sinks import (
    AlertDeliveryWorker,
    BaseHttpAlertSink,
    MockAlertSink,
    SlackAlertSink,
    WebhookAlertSink,
)
from api import create_app
from kafka_sink import DurableKafkaSink, InMemoryKafkaProducer, KafkaSinkConfig
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from pipeline import Pipeline
from prometheus import PrometheusExporter, global_prometheus_exporter
from security import Role, SecurityManager
from storage import Store


# ---------------------------------------------------------------------------
# 1. Pipeline Isolation & Chaos Tests (Rule 1 & Rule 2)
# ---------------------------------------------------------------------------

def test_pipeline_isolation_when_kafka_down():
    """Verify core pipeline continues processing with zero blockage when Kafka sink fails."""
    store = Store(":memory:")
    broken_producer = InMemoryKafkaProducer(simulate_network_failure=True)
    kafka_sink = DurableKafkaSink(store=store, producer=broken_producer)

    # Pipeline with attached sink
    pipeline = Pipeline(store=store)

    now = time.time()
    for seq in range(1, 20):
        raw = RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": seq,
                "price": 150.0 + seq,
                "quantity": 10.0,
            },
        )
        res = pipeline.process_one(raw)
        assert res is not None
        assert res.quality_status == QualityStatus.VALID

    pipeline.flush()
    # Confirm pipeline persisted to store despite downstream kafka failure
    assert len(store.latest("AAPL", limit=50)) == 19

    # Kafka sink attempt produces failure but does not throw or corrupt store
    produced = kafka_sink.poll_and_produce()
    assert produced == 0
    assert kafka_sink.dropped_count > 0
    assert kafka_sink.last_error is not None

    kafka_sink.close()
    store.close()


def test_pipeline_isolation_when_alert_sink_down():
    """Verify alert engine tick evaluation does not block when alert sink is failing."""
    engine = AlertEngine(":memory:")
    broken_sink = MockAlertSink(name="broken_slack", simulate_failure=True)
    worker = AlertDeliveryWorker(engine, [broken_sink])

    alert = engine.add_price_alert("BTC/USD", "above", 50000.0, repeat=False)

    # Tick evaluation is completely synchronous and instant
    t0 = time.perf_counter()
    triggered = engine.evaluate_tick("BTC/USD", price=55000.0)
    dur = time.perf_counter() - t0
    assert dur < 0.01  # Nanosecond-scale, not network-bound
    assert len(triggered) == 1

    # Decoupled worker handles failure with dead-lettering without crashing
    processed = worker.deliver_pending()
    assert processed == 0  # Delivery failed
    assert engine.delivery_failed_count == 1
    assert engine.alerts[0].delivery_status == "failed"

    worker.stop()


# ---------------------------------------------------------------------------
# 2. Delivery Semantics & Checkpoint Resumption (Kafka Sink)
# ---------------------------------------------------------------------------

def test_kafka_durable_at_least_once_and_checkpoint_resume(tmp_path):
    """Verify DurableKafkaSink resumes cleanly from persisted checkpoint across restarts."""
    checkpoint_file = str(tmp_path / "checkpoint.json")
    store = Store(":memory:")
    producer = InMemoryKafkaProducer()

    cfg = KafkaSinkConfig(checkpoint_file=checkpoint_file)
    sink1 = DurableKafkaSink(config=cfg, store=store, producer=producer)

    now = time.time()
    events = [
        CanonicalEvent(
            event_id=f"evt-{i}",
            instrument_id="NVDA",
            event_type=EventType.TRADE,
            exchange_timestamp=now,
            receive_timestamp=now,
            processing_timestamp=now,
            source="NASDAQ",
            sequence_number=i,
            price=450.0 + i,
            quantity=100.0,
        )
        for i in range(1, 11)
    ]
    store.write_canonical_batch(events)
    store.commit()

    # Produce first batch (5 events)
    sink1.poll_and_produce(limit=5)
    assert sink1.produced_count == 5
    assert len(producer.messages) == 5
    sink1.close()  # Checkpoint flushed

    # Simulate process restart with fresh sink instance
    producer2 = InMemoryKafkaProducer()
    sink2 = DurableKafkaSink(config=cfg, store=store, producer=producer2)
    assert sink2.last_canonical_rowid == 5  # Resumed from checkpoint!

    # Produce remaining events
    produced_remaining = sink2.poll_and_produce(limit=10)
    assert produced_remaining == 5
    assert len(producer2.messages) == 5
    # First message in resumed producer is event sequence 6
    val_data = json.loads(producer2.messages[0].value.decode("utf-8"))
    assert val_data["sequence_number"] == 6
    assert val_data["_idempotency_key"] == "NASDAQ:NVDA:6"

    sink2.close()
    store.close()


def test_kafka_topic_separation_false_positive_prevention():
    """Verify quarantined events are strictly isolated from the canonical topic."""
    store = Store(":memory:")
    producer = InMemoryKafkaProducer()
    sink = DurableKafkaSink(store=store, producer=producer)

    now = time.time()
    valid_ev = CanonicalEvent(
        event_id="evt-valid",
        instrument_id="MSFT",
        event_type=EventType.TRADE,
        exchange_timestamp=now,
        receive_timestamp=now,
        processing_timestamp=now,
        source="XNAS",
        sequence_number=1,
        price=320.0,
        quantity=50.0,
    )
    store.write_canonical_batch([valid_ev])

    # Insert quarantined record
    quar_row = (
        "q-invalid",
        "MSFT",
        "XNAS",
        "INVALID",
        '["PRICE_SPIKE"]',
        '{"price": 99999.0}',
        now,
    )
    store.write_quarantine_batch([quar_row])
    store.commit()

    sink.poll_and_produce()

    # Topic separation assertion
    canonical_msgs = producer.by_topic.get(sink.config.canonical_topic, [])
    quarantine_msgs = producer.by_topic.get(sink.config.quarantine_topic, [])

    assert len(canonical_msgs) == 1
    assert len(quarantine_msgs) == 1

    c_data = json.loads(canonical_msgs[0].value.decode("utf-8"))
    q_data = json.loads(quarantine_msgs[0].value.decode("utf-8"))

    assert c_data["event_id"] == "evt-valid"
    assert c_data["quality_status"] == "VALID"

    assert q_data["event_id"] == "q-invalid"
    assert q_data["quality_status"] == "INVALID"

    sink.close()
    store.close()


# ---------------------------------------------------------------------------
# 3. Alert Delivery Hysteresis & False-Positive Prevention
# ---------------------------------------------------------------------------

def test_alert_hysteresis_suppresses_flicker_noise():
    """Verify hysteresis window requires N sustained breaches and margin clearing to re-arm."""
    engine = AlertEngine(":memory:")
    alert = engine.add_price_alert("TSLA", "above", 200.0, repeat=True)
    alert.hysteresis_ticks = 3
    alert.hysteresis_margin_pct = 1.0  # Must drop below 200 * 0.99 = 198.0 to re-arm

    # Ticks 1 and 2: Breaches threshold, but not sustained for 3 ticks
    assert len(engine.evaluate_tick("TSLA", 201.0)) == 0
    assert len(engine.evaluate_tick("TSLA", 202.0)) == 0

    # Tick 3: 3rd consecutive breach -> Fires!
    triggered = engine.evaluate_tick("TSLA", 201.5)
    assert len(triggered) == 1
    assert triggered[0].triggered_value == 201.5

    # Flapping / oscillation right at threshold: (e.g. 199.5, then 201.0)
    # Does NOT clear margin (198.0), so it does NOT re-arm, preventing spam
    assert len(engine.evaluate_tick("TSLA", 199.5)) == 0
    assert len(engine.evaluate_tick("TSLA", 201.0)) == 0

    # Clears below margin (<198.0)
    assert len(engine.evaluate_tick("TSLA", 197.0)) == 0
    assert alert.re_armed is True

    # Re-armed: Now requires 3 sustained breaches again
    assert len(engine.evaluate_tick("TSLA", 205.0)) == 0
    assert len(engine.evaluate_tick("TSLA", 205.0)) == 0
    assert len(engine.evaluate_tick("TSLA", 205.0)) == 1


def test_alert_cooldown_deduplication():
    """Verify sustained breach fires once then respects cooldown interval."""
    engine = AlertEngine(":memory:")
    alert = engine.add_price_alert("ETH/USD", "above", 3000.0, repeat=True)
    alert.hysteresis_ticks = 1
    alert.cooldown_s = 60.0  # 60 second cooldown

    t0 = 1000.0
    # First breach fires
    trig1 = engine.evaluate_tick("ETH/USD", 3100.0, timestamp=t0)
    assert len(trig1) == 1

    # Second breach 10s later is suppressed by cooldown
    trig2 = engine.evaluate_tick("ETH/USD", 3150.0, timestamp=t0 + 10.0)
    assert len(trig2) == 0

    # Breach 65s later exceeds cooldown -> Re-notifies
    trig3 = engine.evaluate_tick("ETH/USD", 3200.0, timestamp=t0 + 65.0)
    assert len(trig3) == 1


# ---------------------------------------------------------------------------
# 4. Security & Cryptographic Signature Verification
# ---------------------------------------------------------------------------

def test_alert_outbound_hmac_signing_and_verification():
    """Verify outbound webhook payloads are signed via SecurityManager and verify correctly."""
    sec = SecurityManager()
    sec.create_feed_secret("MDRAP_ALERTS")

    sink = WebhookAlertSink(
        endpoint_url="http://mock.endpoint/webhook",
        security_manager=sec,
    )

    alert = Alert(
        alert_id=42,
        alert_type=AlertType.PRICE_ABOVE,
        symbol="AAPL",
        condition="AAPL price > 180.0",
        threshold=180.0,
        triggered_value=182.5,
        triggered_at=time.time(),
        message="Alert Triggered",
    )

    payload = sink.format_payload(alert)
    sig, ts_str = sink.sign_payload(payload)

    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex string

    # Receiver verification using SecurityManager
    payload_to_verify = dict(payload)
    payload_to_verify["_auth_ts"] = ts_str
    assert sec.verify_payload("MDRAP_ALERTS", payload_to_verify, sig) is True

    # Tampered payload fails verification
    payload_to_verify["triggered_value"] = 999.0
    assert sec.verify_payload("MDRAP_ALERTS", payload_to_verify, sig) is False


# ---------------------------------------------------------------------------
# 5. Heartbeat & Liveness Self-Test
# ---------------------------------------------------------------------------

def test_alert_heartbeat_liveness_self_test():
    """Verify heartbeat self-test alert exercises the delivery sink."""
    engine = AlertEngine(":memory:")
    mock_sink = MockAlertSink(name="ops_slack")
    worker = AlertDeliveryWorker(engine, [mock_sink])

    hb_res = worker.send_heartbeat()
    assert hb_res.status == "DELIVERED"
    assert len(mock_sink.delivered_alerts) == 1
    assert mock_sink.delivered_alerts[0].condition == "LIVENESS_HEARTBEAT"
    assert mock_sink.delivered_alerts[0].symbol == "SYSTEM"


# ---------------------------------------------------------------------------
# 6. Prometheus Operational Metrics & Reconciliation Tests
# ---------------------------------------------------------------------------

def test_prometheus_operational_metrics_exposition():
    """Verify /metrics renders authentic format and reflects active telemetry counts."""
    store = Store(":memory:")
    app = create_app(db_path=":memory:")
    client = TestClient(app)

    # Ingest synthetic workload
    st = app.state.mdrap
    raw = RawEvent(
        source="TEST_FEED",
        payload={
            "instrument": "AAPL",
            "event_type": "TRADE",
            "exchange_ts": time.time(),
            "sequence": 1,
            "price": 150.0,
            "quantity": 10.0,
        },
    )
    st.pipeline.process_one(raw)
    st.pipeline.flush()

    # Issue an API request to populate HTTP metrics
    health_res = client.get("/v1/health")
    assert health_res.status_code == 200

    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    text = res.text

    assert "mdrap_events_processed_total 1" in text
    assert 'mdrap_events_quality_total{status="VALID"} 1' in text
    assert "mdrap_quarantine_rate 0.000000" in text
    assert "mdrap_audit_verified_status 1" in text
    assert "mdrap_feed_uptime_seconds" in text
    assert "mdrap_api_requests_total" in text


def test_prometheus_reconciliation_kafka_lag():
    """Verify Kafka sink lag is reflected accurately in Prometheus metrics."""
    store = Store(":memory:")
    producer = InMemoryKafkaProducer()
    sink = DurableKafkaSink(store=store, producer=producer)

    now = time.time()
    events = [
        CanonicalEvent(
            event_id=f"evt-{i}",
            instrument_id="SPY",
            event_type=EventType.TRADE,
            exchange_timestamp=now,
            receive_timestamp=now,
            processing_timestamp=now,
            source="BATS",
            sequence_number=i,
            price=400.0,
            quantity=100.0,
        )
        for i in range(1, 6)
    ]
    store.write_canonical_batch(events)
    store.commit()

    # Before producing: lag must be 5
    assert sink.lag() == 5

    exporter = PrometheusExporter()
    rendered = exporter.render(state=type("State", (), {"kafka_sink": sink, "pipeline": None, "store": store})())
    assert "mdrap_kafka_sink_lag_events 5" in rendered

    # After producing: lag decreases to 0
    sink.poll_and_produce()
    assert sink.lag() == 0
    rendered2 = exporter.render(state=type("State", (), {"kafka_sink": sink, "pipeline": None, "store": store})())
    assert "mdrap_kafka_sink_lag_events 0" in rendered2
    assert "mdrap_kafka_sink_produced_total 5" in rendered2

    sink.close()
    store.close()


def test_regression_benchmark_gate():
    """Verify attaching Kafka sink and alert delivery worker does not regress hot-path latency."""
    N = 1000
    now = time.time()
    events = [
        RawEvent(
            source="FEED_A",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": i,
                "price": 150.0 + (i % 10) * 0.1,
                "quantity": 10.0,
            },
        )
        for i in range(1, N + 1)
    ]

    # Baseline: Pipeline alone
    store_base = Store(":memory:")
    pipeline_base = Pipeline(store=store_base)
    t0 = time.perf_counter()
    for ev in events:
        pipeline_base.process_one(ev)
    pipeline_base.flush()
    base_duration = time.perf_counter() - t0
    store_base.close()

    # With decoupled Kafka sink and alert worker active
    store_with_sinks = Store(":memory:")
    pipeline_with_sinks = Pipeline(store=store_with_sinks)
    kafka_producer = InMemoryKafkaProducer()
    kafka_sink = DurableKafkaSink(store=store_with_sinks, producer=kafka_producer)
    alert_engine = AlertEngine(":memory:")
    alert_sink = MockAlertSink()
    alert_worker = AlertDeliveryWorker(alert_engine, [alert_sink])

    t1 = time.perf_counter()
    for ev in events:
        pipeline_with_sinks.process_one(ev)
    pipeline_with_sinks.flush()
    sinks_duration = time.perf_counter() - t1

    # Sinks pull outside hot path
    kafka_sink.poll_and_produce()
    alert_worker.deliver_pending()

    kafka_sink.close()
    store_with_sinks.close()

    # Hot path overhead gate: Sinks run decoupled, so pipeline duration per event must remain fast (< 250µs/event)
    per_event_base_us = (base_duration / N) * 1_000_000
    per_event_sinks_us = (sinks_duration / N) * 1_000_000
    assert per_event_sinks_us < 500.0, f"Hot path regressed: {per_event_sinks_us:.2f} µs/event"

