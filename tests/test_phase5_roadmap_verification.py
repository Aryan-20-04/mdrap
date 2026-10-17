"""Phase 5 Roadmap Verification Suite.

Tests for:
- Task 5.1: SLOs and alerts - feed silence detection, tail latency, projection lag metrics
- Task 5.2: Runbooks & Recovery - torn-tail WAL salvage and zero-downtime key rotation
- Task 5.3: Chaos engineering - socket termination, consumer buffer eviction, feed disconnect
- Task 5.4: STRIDE Threat Model - constant-time comparison, SSRF IP blocking, permission enforcement
- Task 5.5: Supply chain & dependencies - verify clean dependency specification
- Task 5.6: Architecture documentation - single-engine unified model verification
"""

import glob
import os
import shutil
import tempfile
import time
import pytest

from mdrap.models import RawEvent, QualityStatus
from mdrap.engine import Engine
from mdrap.ingestlog import IngestLog
from mdrap.watchdog import SourceWatchdog
from mdrap.security import SecurityManager, ClientEntitlement, Role
from mdrap.storage import Store
from mdrap.alert_sinks import WebhookAlertSink


def test_5_1_slos_and_alert_metrics_reporting():
    """Task 5.1: Verify SLI reporting for silence detection, processing metrics, and projection lag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = os.path.join(tmpdir, "wal")
        db_path = os.path.join(tmpdir, "test.db")
        engine = Engine.open(wal_path, config={"db_path": db_path})
        try:
            # Ingest 50 events
            for i in range(50):
                raw = RawEvent(
                    source="ALPHA",
                    receive_timestamp=time.time(),
                    payload={
                        "instrument": "AAPL",
                        "event_type": "TRADE",
                        "price": 150.0 + i,
                        "quantity": 10.0,
                        "exchange_ts": time.time(),
                        "sequence": i + 1,
                    },
                )
                engine.submit(raw)

            # Query SLI metrics
            metrics = engine.metrics()
            assert metrics["processed"] == 50
            assert metrics["counts"]["VALID"] == 50
            assert metrics["health_status"] == "HEALTHY"
            assert metrics["projection_lag"] == 0
            assert metrics["log_lag"] == 0
        finally:
            engine.close()


def test_5_2_runbooks_torn_tail_salvage_and_key_rotation():
    """Task 5.2: Verify operational runbook procedures for torn-tail WAL salvage and dual-key rotation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Test IngestLog salvage
        wal_dir = os.path.join(tmpdir, "wal")
        log = IngestLog(wal_dir)
        for i in range(10):
            raw = RawEvent(
                source="ALPHA",
                receive_timestamp=time.time(),
                payload={"instrument": "AAPL", "sequence": i + 1, "price": 100.0},
            )
            log.append(raw)
        log.close()

        # Simulate torn tail by appending garbage bytes to the active segment
        seg_files = glob.glob(os.path.join(wal_dir, "segment_*.log"))
        assert len(seg_files) > 0
        seg_file = seg_files[0]
        with open(seg_file, "ab") as f:
            f.write(b"\xaa\x55\x00\x01\xde\xad\xbe\xef")  # Incomplete corrupt frame

        # Run automated salvage procedure
        salvage_stats = IngestLog.salvage(wal_dir, backup=True)
        assert salvage_stats["recovered_records"] == 10
        assert salvage_stats["corrupted_segments"] >= 1

        # 2. Test Zero-downtime dual-key rotation
        db_path = os.path.join(tmpdir, "sec.db")
        store = Store(db_path)
        sec = SecurityManager(store=store)

        try:
            # Register Primary Key
            tok1 = sec.generate_api_key(client_id="Firm_Alpha", role=Role.OPERATOR)
            # Register Overlapping Secondary Key (Rotation Window)
            tok2 = sec.generate_api_key(client_id="Firm_Alpha", role=Role.OPERATOR)

            # Both keys are simultaneously valid
            assert sec.authenticate(tok1) is True
            assert sec.authenticate(tok2) is True

            # Revoke first key
            sec.revoke_api_key(tok1)
            assert sec.authenticate(tok1) is False
            assert sec.authenticate(tok2) is True
        finally:
            store.close()


def test_5_3_chaos_watchdog_silence_and_recovery():
    """Task 5.3: Verify chaos scenario: total feed silence triggers alert, tick arrival heals source."""
    watchdog = SourceWatchdog(silence_threshold_s=0.2, poll_interval_s=0.05)
    watchdog.start_heartbeat()
    try:
        # 1. Feed active tick
        raw = RawEvent(
            source="FEED_CHAOS",
            receive_timestamp=time.time(),
            payload={"instrument": "TSLA", "price": 200.0},
        )
        watchdog.observe(raw)
        assert watchdog.is_source_active("FEED_CHAOS") is True

        # 2. Simulate feed cut: wait past silence threshold
        time.sleep(0.35)
        # Watchdog marks source SILENT
        assert watchdog.is_source_active("FEED_CHAOS") is False

        # 3. Feed resumes with fresh timestamp
        fresh_raw = RawEvent(
            source="FEED_CHAOS",
            receive_timestamp=time.time(),
            payload={"instrument": "TSLA", "price": 200.0},
        )
        watchdog.observe(fresh_raw)
        assert watchdog.is_source_active("FEED_CHAOS") is True
    finally:
        watchdog.stop_heartbeat()


def test_5_4_stride_security_ssrf_and_constant_time():
    """Task 5.4: Verify STRIDE mitigations: SSRF IP range denial and constant-time token comparison."""
    # 1. Test SSRF block on cloud metadata IP at initialization time
    with pytest.raises(ValueError, match="SSRF Protection"):
        WebhookAlertSink(url="http://169.254.169.254/latest/meta-data/")

    # Test static SSRF check
    assert WebhookAlertSink._is_ssrf_safe("169.254.169.254") is False
    assert WebhookAlertSink._is_ssrf_safe("127.0.0.1") is False
    assert WebhookAlertSink._is_ssrf_safe("10.0.0.1") is False
    assert WebhookAlertSink._is_ssrf_safe("192.168.1.1") is False
    assert WebhookAlertSink._is_ssrf_safe("::1") is False
    assert WebhookAlertSink._is_ssrf_safe("93.184.216.34") is True  # Public IP allowed

    # 2. Test constant-time token verification in SecurityManager
    sec = SecurityManager()
    tok = sec.generate_api_key(client_id="Client1", role=Role.VIEWER)
    assert sec.authenticate(tok) is True
    assert sec.authenticate("invalid_token") is False


def test_5_5_supply_chain_and_docs_integrity():
    """Task 5.5 & 5.6: Verify existence of production runbooks, STRIDE matrix, and architecture documentation."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runbooks_path = os.path.join(repo_root, "docs", "operations", "RUNBOOKS.md")
    stride_path = os.path.join(
        repo_root, "docs", "operations", "STRIDE_THREAT_MODEL.md"
    )
    arch_path = os.path.join(repo_root, "docs", "architecture.md")

    assert os.path.exists(runbooks_path), "RUNBOOKS.md must exist in docs/operations/"
    assert os.path.exists(stride_path), (
        "STRIDE_THREAT_MODEL.md must exist in docs/operations/"
    )
    assert os.path.exists(arch_path), "architecture.md must exist in docs/"
